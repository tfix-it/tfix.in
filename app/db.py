import hashlib
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from . import config

# Iteration count for the admin password's PBKDF2-SHA256 hash. 260k is
# OWASP's current baseline recommendation for PBKDF2-HMAC-SHA256 — plenty
# slow against offline guessing while staying instant for a single login.
PBKDF2_ITERATIONS = 260_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    pdf_filename TEXT NOT NULL,
    source_filename TEXT,
    file_type TEXT NOT NULL,
    pages INTEGER NOT NULL,
    pages_range TEXT NOT NULL DEFAULT '',
    sheets INTEGER NOT NULL,
    copies INTEGER NOT NULL,
    color INTEGER NOT NULL,
    doubleside INTEGER NOT NULL,
    duplex_edge TEXT NOT NULL DEFAULT 'long',
    pages_per_sheet INTEGER NOT NULL,
    paper_size TEXT NOT NULL,
    orientation TEXT NOT NULL DEFAULT 'portrait',
    scale_mode TEXT NOT NULL DEFAULT 'fit',
    scale_value REAL,
    auto_printed INTEGER NOT NULL DEFAULT 0,
    daily_seq INTEGER NOT NULL DEFAULT 1,
    files_deleted INTEGER NOT NULL DEFAULT 0,
    cost REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    paid INTEGER NOT NULL DEFAULT 0,
    printer_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS printers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    windows_printer_name TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 1
);

-- Single-row table (id is always 1) holding the shop's auto-print rule:
-- when a new job comes in that satisfies these conditions, it's sent
-- straight to `printer_name` instead of waiting for the admin to click Print.
CREATE TABLE IF NOT EXISTS auto_print_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER NOT NULL DEFAULT 0,
    printer_name TEXT,
    bw_only INTEGER NOT NULL DEFAULT 1,
    max_sheets INTEGER,
    max_copies INTEGER
);

CREATE TABLE IF NOT EXISTS pricing (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_size TEXT NOT NULL,
    color INTEGER NOT NULL,
    singleside_cost REAL NOT NULL,
    doubleside_cost REAL NOT NULL,
    min_cost REAL NOT NULL,
    UNIQUE(paper_size, color)
);

-- Single-row table (id is always 1) holding shop-wide abuse-prevention
-- knobs, all editable from the admin dashboard's "System settings" panel.
CREATE TABLE IF NOT EXISTS app_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    max_upload_mb INTEGER NOT NULL DEFAULT 50,
    unpaid_cleanup_days INTEGER NOT NULL DEFAULT 3,
    rate_limit_window_sec INTEGER NOT NULL DEFAULT 600,
    rate_limit_max_requests INTEGER NOT NULL DEFAULT 10
);

-- Admin login sessions. DB-backed (rather than an in-memory set) so a
-- server restart doesn't silently log the admin out and so this isn't
-- tied to a single process. expires_at is checked on every lookup and
-- pruned lazily — no separate cleanup job needed for a single-shop scale.
CREATE TABLE IF NOT EXISTS admin_sessions (
    token TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

-- The admin password, stored as a salted PBKDF2-SHA256 hash rather than
-- plaintext in config.py. This table (not config.py) is the source of
-- truth once it has a row; config.py's PRINTSHOP_ADMIN_PASS only seeds the
-- very first password (see init_db below), and the admin dashboard's
-- "Change password" panel is the normal way to update it afterwards.
CREATE TABLE IF NOT EXISTS admin_credentials (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    salt TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        # Lightweight migration: jobs tables created before the orientation
        # column existed won't have it yet — CREATE TABLE IF NOT EXISTS
        # above is a no-op on an existing table, so add it here if missing.
        existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
        if "orientation" not in existing_cols:
            conn.execute(
                "ALTER TABLE jobs ADD COLUMN orientation TEXT NOT NULL DEFAULT 'portrait'"
            )
        if "duplex_edge" not in existing_cols:
            conn.execute(
                "ALTER TABLE jobs ADD COLUMN duplex_edge TEXT NOT NULL DEFAULT 'long'"
            )
        if "pages_range" not in existing_cols:
            conn.execute(
                "ALTER TABLE jobs ADD COLUMN pages_range TEXT NOT NULL DEFAULT ''"
            )
        if "scale_mode" not in existing_cols:
            conn.execute(
                "ALTER TABLE jobs ADD COLUMN scale_mode TEXT NOT NULL DEFAULT 'fit'"
            )
        if "scale_value" not in existing_cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN scale_value REAL")
        if "auto_printed" not in existing_cols:
            conn.execute(
                "ALTER TABLE jobs ADD COLUMN auto_printed INTEGER NOT NULL DEFAULT 0"
            )
        if "daily_seq" not in existing_cols:
            conn.execute(
                "ALTER TABLE jobs ADD COLUMN daily_seq INTEGER NOT NULL DEFAULT 1"
            )
        if "source_filename" not in existing_cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN source_filename TEXT")
        if "files_deleted" not in existing_cols:
            conn.execute(
                "ALTER TABLE jobs ADD COLUMN files_deleted INTEGER NOT NULL DEFAULT 0"
            )
        # auto_print_settings existed before with a pages-based cap; migrate
        # to a sheets-based cap (sheets already account for pages-per-sheet,
        # duplex and copies, so it's the actual print-run size).
        aps_cols = {row["name"] for row in conn.execute("PRAGMA table_info(auto_print_settings)")}
        if aps_cols and "max_sheets" not in aps_cols:
            conn.execute("ALTER TABLE auto_print_settings ADD COLUMN max_sheets INTEGER")
            if "max_pages" in aps_cols:
                conn.execute("UPDATE auto_print_settings SET max_sheets = max_pages WHERE id=1")
        existing = conn.execute("SELECT COUNT(*) AS c FROM pricing").fetchone()["c"]
        if existing == 0:
            conn.executemany(
                "INSERT INTO pricing (paper_size, color, singleside_cost, doubleside_cost, min_cost) "
                "VALUES (?, ?, ?, ?, ?)",
                config.DEFAULT_PRICING,
            )
        # Seed the single auto-print settings row (off by default) if absent.
        conn.execute(
            "INSERT OR IGNORE INTO auto_print_settings (id, enabled, printer_name, bw_only, max_sheets, max_copies) "
            "VALUES (1, 0, NULL, 1, 10, NULL)"
        )
        # Seed the single app-settings row from config.py defaults if absent
        # — after this, the admin dashboard is the source of truth.
        conn.execute(
            "INSERT OR IGNORE INTO app_settings "
            "(id, max_upload_mb, unpaid_cleanup_days, rate_limit_window_sec, rate_limit_max_requests) "
            "VALUES (1, ?, ?, ?, ?)",
            (
                config.DEFAULT_MAX_UPLOAD_MB,
                config.DEFAULT_UNPAID_CLEANUP_DAYS,
                config.DEFAULT_RATE_LIMIT_WINDOW_SEC,
                config.DEFAULT_RATE_LIMIT_MAX_REQUESTS,
            ),
        )

    # Seed (or, if requested, force-reset) the admin password. This runs
    # after the block above so it's its own transaction — set_admin_password
    # below opens its own connection rather than reusing `conn`.
    existing_cred = get_admin_password_hash()
    if not existing_cred or config.RESET_ADMIN_PASSWORD:
        seed_password = config.INITIAL_ADMIN_PASSWORD or secrets.token_urlsafe(12)
        set_admin_password(seed_password)
        if existing_cred and config.RESET_ADMIN_PASSWORD:
            reason = "reset (PRINTSHOP_RESET_ADMIN_PASSWORD=1)"
        else:
            reason = "set for first-time setup"
        if config.INITIAL_ADMIN_PASSWORD:
            print(f"[admin] Admin password {reason} from PRINTSHOP_ADMIN_PASS.")
        else:
            print("=" * 60)
            print(f"[admin] Admin password {reason} — PRINTSHOP_ADMIN_PASS wasn't set.")
            print(f"[admin] Username: {config.ADMIN_USERNAME}")
            print(f"[admin] Generated password: {seed_password}")
            print("[admin] Sign in with this once, then set a real password")
            print("[admin] from the dashboard's Account panel.")
            print("=" * 60)


def now():
    return datetime.utcnow().isoformat()


# ---------------- pricing ----------------
def get_rates(paper_size: str, color: bool):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM pricing WHERE paper_size=? AND color=?",
            (paper_size, int(color)),
        ).fetchone()
        return dict(row) if row else None


def list_pricing():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM pricing ORDER BY paper_size, color"
        ).fetchall()
        return [dict(r) for r in rows]


def update_pricing(paper_size, color, singleside_cost, doubleside_cost, min_cost):
    with get_conn() as conn:
        conn.execute(
            "UPDATE pricing SET singleside_cost=?, doubleside_cost=?, min_cost=? "
            "WHERE paper_size=? AND color=?",
            (singleside_cost, doubleside_cost, min_cost, paper_size, int(color)),
        )


# ---------------- jobs ----------------
def create_job(**fields):
    fields["created_at"] = fields["updated_at"] = now()
    cols = ", ".join(fields.keys())
    placeholders = ", ".join("?" for _ in fields)
    with get_conn() as conn:
        cur = conn.execute(
            f"INSERT INTO jobs ({cols}) VALUES ({placeholders})",
            tuple(fields.values()),
        )
        return cur.lastrowid


def count_jobs_by_phone_today(phone: str) -> int:
    """How many jobs this phone number has already submitted today (UTC,
    matching how created_at is stored) — used to assign the next job's
    daily_seq, i.e. its position in "phone-Nth job of the day", and to
    decide whether the minimum-charge floor still applies (see pricing.py /
    main.py, which waive it from the phone's 2nd job of the day onward)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM jobs WHERE phone=? AND date(created_at)=date('now')",
            (phone,),
        ).fetchone()
        return row["c"]


def list_jobs_by_phone_today(phone: str):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE phone=? AND date(created_at)=date('now') "
            "ORDER BY created_at DESC",
            (phone,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_job(job_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None


def list_jobs(status=None):
    with get_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status=? ORDER BY created_at DESC", (status,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]


def update_job(job_id, **fields):
    fields["updated_at"] = now()
    set_clause = ", ".join(f"{k}=?" for k in fields)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE jobs SET {set_clause} WHERE id=?",
            tuple(fields.values()) + (job_id,),
        )
    return get_job(job_id)


def list_unpaid_jobs_for_cleanup(days: int):
    """Unpaid jobs older than `days` whose files haven't already been
    swept. Used by the background cleanup loop."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE paid=0 AND files_deleted=0 "
            "AND datetime(created_at) <= datetime('now', ?)",
            (f"-{int(days)} days",),
        ).fetchall()
        return [dict(r) for r in rows]


def mark_job_files_deleted(job_id):
    with get_conn() as conn:
        conn.execute(
            "UPDATE jobs SET files_deleted=1, updated_at=? WHERE id=?",
            (now(), job_id),
        )


# ---------------- printers ----------------
def upsert_printer(name, windows_printer_name):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO printers (name, windows_printer_name) VALUES (?, ?) "
            "ON CONFLICT(windows_printer_name) DO UPDATE SET name=excluded.name",
            (name, windows_printer_name),
        )


def list_printers():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM printers").fetchall()
        return [dict(r) for r in rows]


def set_printer_enabled(printer_id, enabled: bool):
    with get_conn() as conn:
        conn.execute(
            "UPDATE printers SET enabled=? WHERE id=?", (int(enabled), printer_id)
        )


# ---------- auto-print settings ----------
def get_auto_print_settings():
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM auto_print_settings WHERE id=1"
        ).fetchone()
        return dict(row) if row else None


def update_auto_print_settings(enabled, printer_name, bw_only, max_sheets, max_copies):
    with get_conn() as conn:
        conn.execute(
            "UPDATE auto_print_settings SET enabled=?, printer_name=?, bw_only=?, "
            "max_sheets=?, max_copies=? WHERE id=1",
            (int(enabled), printer_name, int(bw_only), max_sheets, max_copies),
        )


# ---------- app settings (upload cap, cleanup, rate limiting) ----------
def get_app_settings():
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM app_settings WHERE id=1").fetchone()
        return dict(row) if row else None


def update_app_settings(
    max_upload_mb, unpaid_cleanup_days, rate_limit_window_sec, rate_limit_max_requests
):
    with get_conn() as conn:
        conn.execute(
            "UPDATE app_settings SET max_upload_mb=?, unpaid_cleanup_days=?, "
            "rate_limit_window_sec=?, rate_limit_max_requests=? WHERE id=1",
            (
                max_upload_mb,
                unpaid_cleanup_days,
                rate_limit_window_sec,
                rate_limit_max_requests,
            ),
        )
    return get_app_settings()


# ---------- admin sessions ----------
def create_session(token: str, ttl_seconds: int):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO admin_sessions (token, created_at, expires_at) "
            "VALUES (?, ?, datetime('now', ?))",
            (token, now(), f"+{int(ttl_seconds)} seconds"),
        )


def get_valid_session(token: str):
    """Return the session row if `token` exists and hasn't expired, else
    None. Also opportunistically deletes expired sessions so the table
    doesn't grow unbounded — cheap enough to do on every check at this
    scale, no separate sweep needed."""
    if not token:
        return None
    with get_conn() as conn:
        conn.execute("DELETE FROM admin_sessions WHERE expires_at <= datetime('now')")
        row = conn.execute(
            "SELECT * FROM admin_sessions WHERE token=? AND expires_at > datetime('now')",
            (token,),
        ).fetchone()
        return dict(row) if row else None


def delete_session(token: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM admin_sessions WHERE token=?", (token,))


def delete_all_sessions():
    """Log every admin device out at once — used after a password change,
    since anyone still holding a session cookie should have to prove they
    know the new password too."""
    with get_conn() as conn:
        conn.execute("DELETE FROM admin_sessions")


# ---------- admin password (salted PBKDF2-SHA256, not plaintext) ----------
def _hash_password(password: str, salt: bytes = None) -> tuple[bytes, bytes]:
    if salt is None:
        salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return salt, digest


def get_admin_password_hash():
    """Return the stored {salt, password_hash, updated_at} row, or None if
    no password has ever been set (a brand-new, empty database)."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM admin_credentials WHERE id=1").fetchone()
        return dict(row) if row else None


def set_admin_password(password: str):
    salt, digest = _hash_password(password)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO admin_credentials (id, salt, password_hash, updated_at) "
            "VALUES (1, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET salt=excluded.salt, "
            "password_hash=excluded.password_hash, updated_at=excluded.updated_at",
            (salt.hex(), digest.hex(), now()),
        )


def verify_admin_password(password: str) -> bool:
    row = get_admin_password_hash()
    if not row:
        return False
    salt = bytes.fromhex(row["salt"])
    _, digest = _hash_password(password, salt)
    # compare_digest, not ==, so a wrong guess can't be timed to narrow
    # down how many bytes of the hash matched.
    return secrets.compare_digest(digest.hex(), row["password_hash"])
