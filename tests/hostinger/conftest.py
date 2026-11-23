"""
Test harness for the Hostinger (PHP + MySQL) version of T-PRINT.

Runs the real PHP code under `php -S` (with router.php standing in for
.htaccess) against a MySQL/MariaDB database. Needs: php 8.1+ with pdo_mysql
and gd, and a database reachable with the TPRINT_TEST_DB_* settings below.
The whole module is skipped when either is missing.
"""
import io
import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest

REPO = Path(__file__).resolve().parents[2]
DOCROOT = REPO / "hostinger"
DB = {
    "host": os.environ.get("TPRINT_TEST_DB_HOST", "127.0.0.1"),
    "port": int(os.environ.get("TPRINT_TEST_DB_PORT", "3306")),
    "name": os.environ.get("TPRINT_TEST_DB_NAME", "tprint_test"),
    "user": os.environ.get("TPRINT_TEST_DB_USER", "tprint"),
    "pass": os.environ.get("TPRINT_TEST_DB_PASS", "tprint-test-pw"),
}
SETUP_CODE = "setup-" + "c" * 30
AGENT_KEY = "agent-" + "k" * 40
MCP_TOKEN = "mcp-" + "t" * 40
ADMIN_PASSWORD = "correct-horse-battery"
ORIGIN = "https://print.tfix.in"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _php_ok():
    if not shutil.which("php"):
        return False
    mods = subprocess.run(["php", "-m"], capture_output=True, text=True).stdout.lower()
    return "pdo_mysql" in mods and "gd" in mods


def _php(code):
    return subprocess.run(["php", "-r", code], capture_output=True, text=True)


def _db_ok():
    r = _php(f"new PDO('mysql:host={DB['host']};port={DB['port']};dbname={DB['name']}', '{DB['user']}', '{DB['pass']}'); echo 'ok';")
    return r.stdout.strip() == "ok"


if not (_php_ok() and _db_ok()):
    pytest.skip("PHP with pdo_mysql/gd and a test MySQL database are required", allow_module_level=True)


class Server:
    def __init__(self, tmp):
        self.tmp = tmp
        self.config_path = tmp / "tprint-config.php"
        self.storage = tmp / "storage"
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.write_config()
        env = {**os.environ, "TPRINT_CONFIG": str(self.config_path), "TPRINT_DOCROOT": str(DOCROOT)}
        self.proc = subprocess.Popen(
            ["php", "-d", "upload_max_filesize=64M", "-d", "post_max_size=70M", "-S", f"127.0.0.1:{self.port}",
             "-t", str(DOCROOT), str(REPO / "tests/hostinger/router.php")],
            env=env, stdout=subprocess.DEVNULL, stderr=open(tmp / "php.log", "w"))
        for _ in range(50):
            try:
                httpx.get(self.base + "/index.html", timeout=1)
                break
            except httpx.TransportError:
                time.sleep(0.1)

    def write_config(self, **overrides):
        cfg = {
            "db": DB, "public_origin": ORIGIN, "storage_dir": str(self.storage), "setup_code": SETUP_CODE,
            "admin_username": "admin", "agent_key": AGENT_KEY, "mcp_token": MCP_TOKEN, "mcp_allow_print": False,
            "timezone": "Asia/Kolkata", "trust_forwarded_for": True, "agent_offline_after_sec": 45,
        }
        cfg.update(overrides)
        (self.tmp / "config.json").write_text(json.dumps(cfg))
        self.config_path.write_text("<?php return json_decode(file_get_contents(__DIR__ . '/config.json'), true);\n")

    def stop(self):
        self.proc.terminate()
        self.proc.wait(5)


@pytest.fixture(scope="session")
def server(tmp_path_factory):
    srv = Server(tmp_path_factory.mktemp("tprint"))
    yield srv
    srv.stop()


@pytest.fixture(autouse=True)
def fresh(server):
    """Empty database and storage before every test; default config."""
    server.write_config()
    tables = ["schema_info", "uploads", "jobs", "printers", "pricing", "auto_print_settings", "app_settings",
              "admin_credentials", "admin_sessions", "print_dispatches", "payment_audit", "rate_hits", "agents"]
    drop = "".join(f"$p->exec('DROP TABLE IF EXISTS {t}');" for t in tables)
    r = _php(f"$p = new PDO('mysql:host={DB['host']};port={DB['port']};dbname={DB['name']}', '{DB['user']}', '{DB['pass']}');{drop}")
    assert r.returncode == 0, r.stderr
    shutil.rmtree(server.storage, ignore_errors=True)
    yield


def sql(query, params=()):
    """Run SQL directly (test setup/inspection). Returns rows as dicts."""
    code = (f"$p = new PDO('mysql:host={DB['host']};port={DB['port']};dbname={DB['name']}', '{DB['user']}', '{DB['pass']}');"
            f"$s = $p->prepare({json.dumps(query)}); $s->execute(json_decode({json.dumps(json.dumps(list(params)))}, true));"
            "echo json_encode($s->columnCount() ? $s->fetchAll(PDO::FETCH_ASSOC) : []);")
    r = _php(code)
    assert r.returncode == 0, r.stderr + r.stdout
    return json.loads(r.stdout or "[]")


@pytest.fixture
def client(server):
    with httpx.Client(base_url=server.base, timeout=20, headers={"X-Forwarded-For": "203.0.113.10"}) as c:
        yield c


@pytest.fixture
def admin(server):
    """A client logged in as the shop admin (first-time setup done)."""
    with httpx.Client(base_url=server.base, timeout=20, headers={"Origin": ORIGIN, "X-Forwarded-For": "203.0.113.20"}) as c:
        r = c.post("/api/admin/setup", data={"setup_code": SETUP_CODE, "new_password": ADMIN_PASSWORD})
        assert r.status_code == 200, r.text
        # PHP marks the cookie Secure (public origin is https); copy it for the plain-http test server.
        c.cookies.set("tprint_admin", r.cookies.get("tprint_admin") or _cookie_from(r))
        yield c


def _cookie_from(response):
    for h in response.headers.get_list("set-cookie"):
        if h.startswith("tprint_admin="):
            return h.split(";", 1)[0].split("=", 1)[1]
    raise AssertionError("no session cookie")


@pytest.fixture
def agent(server):
    with httpx.Client(base_url=server.base, timeout=20, headers={"Authorization": "Bearer " + AGENT_KEY}) as c:
        yield c


def agent_poll(agent, printers=(("HP LaserJet", "ready"), ("Epson Colour", "paper_out")), name=None):
    headers = {"X-TPrint-Agent": name} if name else {}
    r = agent.post("/api/agent/poll", headers=headers, json={"agent_version": "test", "printers": [{"name": n, "status": s} for n, s in printers]})
    assert r.status_code == 200, r.text
    return r.json()


def make_pdf(pages=3):
    """A tiny valid PDF with N pages (no external libraries)."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>",
            "<< /Type /Pages /Kids [" + " ".join(f"{3 + i} 0 R" for i in range(pages)) + f"] /Count {pages} >>"]
    objs += ["<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] >>"] * pages
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs):
        offsets.append(out.tell())
        out.write(f"{i + 1} 0 obj\n{o}\nendobj\n".encode())
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer << /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def upload(client, name="doc.pdf", data=None, ctype="application/pdf"):
    return client.post("/api/upload", files={"file": (name, data if data is not None else make_pdf(), ctype)})


def submit(client, upload_id, phone="9876543210", **opts):
    form = {"phone": phone, "pdf_filename": upload_id, "pages_range": "", "color": "false", "doubleside": "false",
            "duplex_edge": "long", "pages_per_sheet": "1", "copies": "1", "paper_size": "A4", "orientation": "portrait",
            "scale_mode": "fit", "pages": "1", "original_filename": "x", "file_type": "pdf"}
    form.update({k: str(v) for k, v in opts.items()})
    return client.post("/api/jobs", data=form)
