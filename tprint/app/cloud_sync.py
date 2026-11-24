"""
Online orders from print.tfix.in -> the local T Print queue.

Customers can order on print.tfix.in while the shop is closed; the orders
wait on the website. When this server runs (shop PC on), a background loop:

  1. fetches pending online orders (outbound HTTPS only — nothing on the shop
     PC is exposed to the internet),
  2. downloads each file, converts it to PDF, counts the pages and prices it
     with the shop's own pricing table, exactly like an in-shop upload,
  3. adds each file as a normal job: status "queued", paid = 0. It prints
     only after staff mark it Paid (the print gate in print_service.py),
  4. tells the website the order was received (the website then deletes its
     copy of the files), and
  5. keeps the website's order status in step with the local jobs, so the
     customer can follow it: received -> paid -> printing -> ready -> collected.

Configure with environment variables (see START_HERE.md):
  PRINTSHOP_CLOUD_URL    e.g. https://print.tfix.in/api/shop.php
  PRINTSHOP_CLOUD_TOKEN  the shop token (the website stores only its SHA-256)
"""
import asyncio
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from . import config, convert, db, pricing

MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
ALLOWED_PAGES_PER_SHEET = {1, 2, 4, 6, 9, 16}

SCHEMA = """
CREATE TABLE IF NOT EXISTS cloud_orders (
    cloud_order_id INTEGER PRIMARY KEY,
    order_code TEXT NOT NULL,
    acked INTEGER NOT NULL DEFAULT 0,
    last_pushed TEXT,
    done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cloud_imports (
    cloud_file_id INTEGER PRIMARY KEY,
    cloud_order_id INTEGER NOT NULL,
    job_id INTEGER,
    error TEXT,
    created_at TEXT NOT NULL
);
"""


def _setting(name: str, env: str, default=""):
    """Read from app/config.py if it has the setting, else from the environment —
    so an older, customised config.py without these lines keeps working."""
    return getattr(config, name, None) or os.environ.get(env, default)


def cloud_url() -> str:
    return _setting("CLOUD_URL", "PRINTSHOP_CLOUD_URL")


def cloud_token() -> str:
    return _setting("CLOUD_TOKEN", "PRINTSHOP_CLOUD_TOKEN")


def sync_interval() -> int:
    return int(_setting("CLOUD_SYNC_INTERVAL_SEC", "PRINTSHOP_CLOUD_SYNC_INTERVAL_SEC", 60))


class CloudError(RuntimeError):
    pass


class CloudClient:
    def __init__(self, base_url: str, token: str, timeout: int = 60):
        self.base_url = base_url
        self.token = token
        self.timeout = timeout

    def _request(self, method: str, action: str, params: Optional[dict] = None,
                 body: Optional[dict] = None, limit: int = 4 * 1024 * 1024) -> bytes:
        query = urllib.parse.urlencode({"action": action, **(params or {})})
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.base_url}?{query}", data=data, method=method)
        req.add_header("X-TPrint-Token", self.token)
        req.add_header("User-Agent", "TPrint-Shop/1.0")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = resp.read(limit + 1)
        except urllib.error.HTTPError as e:
            raise CloudError(f"{action}: HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise CloudError(f"{action}: {e}") from e
        if len(payload) > limit:
            raise CloudError(f"{action}: response too large")
        return payload

    def _json(self, *args, **kwargs) -> dict:
        try:
            return json.loads(self._request(*args, **kwargs))
        except ValueError as e:
            raise CloudError("invalid JSON from website") from e

    def pending(self) -> list:
        return self._json("GET", "pending").get("orders", [])

    def download(self, file_id: int) -> bytes:
        return self._request("GET", "file", {"id": file_id}, limit=MAX_DOWNLOAD_BYTES)

    def ack(self, order_id: int, files: list) -> None:
        self._json("POST", "ack", body={"order_id": order_id, "files": files})

    def push_status(self, orders: list) -> None:
        self._json("POST", "status", body={"orders": orders})

    def reset_pin(self, phone: str) -> dict:
        """Staff reset of an online customer's CLUTCHPRINT PIN; returns {pin, phone}."""
        return self._json("POST", "reset_pin", body={"phone": phone})


def initialize():
    with db.get_conn() as conn:
        conn.executescript(SCHEMA)


def is_configured() -> bool:
    return bool(cloud_url() and cloud_token())


# ---------------------------------------------------------------- import
def _job_label(order: dict, original_name: str) -> str:
    label = f"[{order['code']} · {order['name']}] {original_name}"
    if order.get("notes"):
        label += f" — Note: {order['notes'][:150]}"
    return label


def _create_job_from_file(order: dict, f: dict, content: bytes) -> int:
    """Same steps as an in-shop upload + submit. Raises ValueError with a
    customer-friendly message when the file can't be used."""
    from .routes_customer import _parse_pages_range  # shared page-range rules

    pps = int(f.get("pages_per_sheet") or 1)
    copies = int(f.get("copies") or 1)
    paper_size = f.get("paper_size") if f.get("paper_size") in ("A4", "A3") else None
    if pps not in ALLOWED_PAGES_PER_SHEET or not 1 <= copies <= 500 or not paper_size:
        raise ValueError("Print options not valid")
    orientation = f.get("orientation") if f.get("orientation") in ("portrait", "landscape") else "portrait"
    duplex_edge = f.get("duplex_edge") if f.get("duplex_edge") in ("long", "short") else "long"

    saved = convert.save_upload(content, f["original_name"])
    try:
        pdf_path = convert.convert_to_pdf(saved)
        total_pages = pricing.get_page_count(str(pdf_path))
    except Exception as e:
        raise ValueError("Could not open this file — please send it as PDF") from e
    selected = _parse_pages_range(f.get("pages_range") or "", total_pages)  # ValueError if out of range
    pages_range = "" if len(selected) == total_pages else (f.get("pages_range") or "")

    phone = order["phone"]
    daily_seq = db.count_jobs_by_phone_today(phone) + 1
    total_cost, total_sheets = pricing.print_cost(
        len(selected), pps, bool(f.get("color")), bool(f.get("doubleside")), copies, paper_size,
        apply_min_cost=daily_seq == 1,
    )
    return db.create_job(
        phone=phone,
        original_filename=_job_label(order, f["original_name"]),
        pdf_filename=pdf_path.name,
        source_filename=saved.name if saved != pdf_path else None,
        file_type=(f.get("ext") or "").lower(),
        pages=len(selected),
        pages_range=pages_range,
        sheets=total_sheets,
        copies=copies,
        color=int(bool(f.get("color"))),
        doubleside=int(bool(f.get("doubleside"))),
        duplex_edge=duplex_edge,
        pages_per_sheet=pps,
        paper_size=paper_size,
        orientation=orientation,
        scale_mode="fit",
        scale_value=None,
        auto_printed=0,
        daily_seq=daily_seq,
        cost=round(total_cost, 2),
        status="queued",
        paid=0,
        printer_id=None,
    )


def import_order(client: CloudClient, order: dict) -> list:
    """Import one online order. Returns the new local jobs. Safe to call again
    for the same order: files already imported are not imported twice."""
    created = []
    results = []
    with db.get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO cloud_orders (cloud_order_id, order_code, created_at) VALUES (?, ?, ?)",
            (order["order_id"], order["code"], db.now()),
        )
    for f in order["files"]:
        fid = int(f["file_id"])
        with db.get_conn() as conn:
            row = conn.execute("SELECT * FROM cloud_imports WHERE cloud_file_id=?", (fid,)).fetchone()
        if row is not None:
            # Already handled (or interrupted mid-import: don't risk a duplicate job).
            job_id, error = row["job_id"], row["error"]
            if job_id is None and error is None:
                error = "Import was interrupted — please check with the shop"
        elif not f.get("available", True):
            job_id, error = None, "File no longer available online"
            _record(fid, order["order_id"], None, error)
        else:
            content = client.download(fid)  # network errors abort; retried next cycle
            _record(fid, order["order_id"], None, None)  # mark "in progress" first
            try:
                job_id, error = _create_job_from_file(order, f, content), None
                created.append(db.get_job(job_id))
            except ValueError as e:
                job_id, error = None, str(e)[:200]
            _record(fid, order["order_id"], job_id, error)
        job = db.get_job(job_id) if job_id else None
        results.append({
            "file_id": fid, "job_id": job_id, "error": error,
            "pages": job["pages"] if job else None,
            "cost": job["cost"] if job else None,
        })
    client.ack(order["order_id"], results)
    with db.get_conn() as conn:
        conn.execute("UPDATE cloud_orders SET acked=1 WHERE cloud_order_id=?", (order["order_id"],))
    return created


def _record(file_id: int, order_id: int, job_id, error):
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO cloud_imports (cloud_file_id, cloud_order_id, job_id, error, created_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(cloud_file_id) DO UPDATE SET job_id=excluded.job_id, error=excluded.error",
            (file_id, order_id, job_id, error, db.now()),
        )


# ---------------------------------------------------------------- status
def order_status(jobs: list) -> str:
    """Overall online status from the local jobs of one order."""
    if not jobs:
        return "received"
    statuses = [j["status"] for j in jobs]
    if all(s == "collected" for s in statuses):
        return "collected"
    if all(s in ("ready", "collected") for s in statuses):
        return "ready"
    if any(s == "printing" for s in statuses):
        return "printing"
    if all(j["paid"] for j in jobs):
        return "paid"
    return "received"


def push_statuses(client: CloudClient) -> int:
    """Send changed order statuses to the website. Returns how many were sent."""
    with db.get_conn() as conn:
        orders = conn.execute("SELECT * FROM cloud_orders WHERE acked=1 AND done=0").fetchall()
    updates, snapshots = [], {}
    for o in orders:
        with db.get_conn() as conn:
            imports = conn.execute(
                "SELECT * FROM cloud_imports WHERE cloud_order_id=? AND job_id IS NOT NULL",
                (o["cloud_order_id"],),
            ).fetchall()
        files, jobs = [], []
        for imp in imports:
            job = db.get_job(imp["job_id"])
            if job:
                jobs.append(job)
                files.append({"file_id": imp["cloud_file_id"], "job_status": job["status"], "paid": bool(job["paid"])})
        status = order_status(jobs)
        update = {"order_id": o["cloud_order_id"], "status": status, "files": files}
        snapshot = json.dumps(update, sort_keys=True)
        if snapshot != o["last_pushed"]:
            updates.append(update)
            snapshots[o["cloud_order_id"]] = (snapshot, status)
    if updates:
        client.push_status(updates)
        with db.get_conn() as conn:
            for oid, (snapshot, status) in snapshots.items():
                conn.execute(
                    "UPDATE cloud_orders SET last_pushed=?, done=? WHERE cloud_order_id=?",
                    (snapshot, int(status == "collected"), oid),
                )
    return len(updates)


def sync_once(client: CloudClient) -> list:
    """One full cycle. Returns the newly created local jobs."""
    created = []
    for order in client.pending():
        try:
            created.extend(import_order(client, order))
        except CloudError as e:
            print(f"[cloud] order {order.get('code')}: {e} — will retry")
    push_statuses(client)
    return created


# ---------------------------------------------------------------- loop
_task: Optional[asyncio.Task] = None


async def _loop():
    from .ws_hub import hub
    client = CloudClient(cloud_url(), cloud_token())
    interval = sync_interval()
    print(f"[cloud] syncing online orders from {cloud_url()} every {interval}s")
    while True:
        try:
            created = await asyncio.to_thread(sync_once, client)
            for job in created:
                await hub.broadcast_job(job)
            if created:
                print(f"[cloud] imported {len(created)} online job(s)")
        except Exception as e:  # keep the shop server running whatever happens online
            print(f"[cloud] sync error: {e}")
        await asyncio.sleep(interval)


def start():
    global _task
    if is_configured():
        _task = asyncio.create_task(_loop())
    else:
        print("[cloud] online orders off (set PRINTSHOP_CLOUD_URL and PRINTSHOP_CLOUD_TOKEN to enable)")


def stop():
    global _task
    if _task:
        _task.cancel()
        _task = None
