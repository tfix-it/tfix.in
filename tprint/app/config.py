import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# --- Storage ---
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
DB_PATH = BASE_DIR / "printshop.db"

# --- Server ---
HOST = "0.0.0.0"
# 443 is HTTPS's default port, so browsers omit it from the address bar —
# https://printatshop.local instead of https://printatshop.local:8000.
# Binding to it (like port 80 below) needs admin/root privileges — see the
# note in the README / bottom of main.py's __main__ block.
PORT = int(os.environ.get("PRINTSHOP_PORT", 443))
MDNS_HOSTNAME = os.environ.get("PRINTSHOP_MDNS_NAME", "printatshop")  # -> printatshop.local

# --- TLS (HTTPS) — cert/key generated for printatshop.local ---
SSL_CERT_PATH = os.environ.get("PRINTSHOP_SSL_CERT", str(BASE_DIR / "cert.pem"))
SSL_KEY_PATH = os.environ.get("PRINTSHOP_SSL_KEY", str(BASE_DIR / "key.pem"))
# Only disable this (set env var to "0") for local HTTP-only testing.
# In production the admin session cookie must only ever travel over HTTPS.
FORCE_SECURE_COOKIE = os.environ.get("PRINTSHOP_FORCE_SECURE_COOKIE", "1") == "1"

# --- HTTP -> HTTPS redirect ---
# The main app above is TLS-only (uvicorn can't serve plain HTTP and HTTPS
# on the same listener), so anyone who types "http://" or follows an old
# bookmark would otherwise just get a connection failure — or worse, if a
# future change ever adds a plain HTTP listener, would silently submit the
# admin login unencrypted. This spins up a second, tiny listener whose only
# job is a 301 redirect over to the HTTPS site.
ENABLE_HTTP_REDIRECT = os.environ.get("PRINTSHOP_ENABLE_HTTP_REDIRECT", "1") == "1"
HTTP_REDIRECT_PORT = int(os.environ.get("PRINTSHOP_HTTP_REDIRECT_PORT", 80))

# --- External tools — EDIT THESE PATHS for your Windows machine ---
# Found automatically in the usual install places; set SUMATRA_PATH / LIBREOFFICE_PATH
# in tprint.env only if they are somewhere else. (The SumatraPDF installer puts it in
# the user's AppData\Local by default, not in Program Files.)
def _find_exe(env_name, candidates):
    given = os.environ.get(env_name, "").strip().strip('"')
    if given:
        return given
    for c in candidates:
        p = os.path.expandvars(c)
        if "%" not in p and os.path.isfile(p):
            return p
    return os.path.expandvars(candidates[0])


SUMATRA_PATH = _find_exe("SUMATRA_PATH", [
    r"C:\Program Files\SumatraPDF\SumatraPDF.exe",
    r"%LOCALAPPDATA%\SumatraPDF\SumatraPDF.exe",
    r"C:\Program Files (x86)\SumatraPDF\SumatraPDF.exe",
    str(BASE_DIR / "SumatraPDF.exe"),
    str(BASE_DIR / "tools" / "SumatraPDF.exe"),
])
LIBREOFFICE_PATH = _find_exe("LIBREOFFICE_PATH", [
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
])

# --- Admin auth ---
ADMIN_USERNAME = os.environ.get("PRINTSHOP_ADMIN_USER", "admin")

# The admin password itself is no longer kept here — it's stored as a
# salted hash in the database (db.py's admin_credentials table) so it can
# be changed from the dashboard's Account panel without touching config or
# restarting. These two env vars only matter for bootstrapping:
#   PRINTSHOP_ADMIN_PASS               seeds the very first password on a
#                                       fresh database. If unset, a random
#                                       one is generated and printed to the
#                                       console on first run.
#   PRINTSHOP_RESET_ADMIN_PASSWORD=1   the "I forgot my password" recovery
#                                       path — forces PRINTSHOP_ADMIN_PASS
#                                       (or a fresh random one) to overwrite
#                                       whatever's currently stored. Unset
#                                       it again after recovering.
INITIAL_ADMIN_PASSWORD = os.environ.get("PRINTSHOP_ADMIN_PASS")
RESET_ADMIN_PASSWORD = os.environ.get("PRINTSHOP_RESET_ADMIN_PASSWORD", "0") == "1"

SESSION_COOKIE_NAME = "printshop_admin_session"

# --- Seed pricing (₹) used only the first time the DB is created.
# Admin can edit all of this later from the dashboard. ---
# (paper_size, color[0/1], singleside_cost, doubleside_cost, min_cost)
DEFAULT_PRICING = [
    ("A4", 0, 2, 3, 10),
    ("A4", 1, 10, 15, 10),
    ("A3", 0, 5, 7, 15),
    ("A3", 1, 15, 20, 15),
]

# --- Seed values for admin-adjustable system settings (app_settings table).
# Only used the first time the DB is created — after that, edit these from
# the admin dashboard's "System settings" panel, not here. ---
DEFAULT_MAX_UPLOAD_MB = 50
DEFAULT_UNPAID_CLEANUP_DAYS = 3
DEFAULT_RATE_LIMIT_WINDOW_SEC = 600     # 10 minutes
DEFAULT_RATE_LIMIT_MAX_REQUESTS = 10    # per IP, shared across upload+submit

# How often the background cleanup sweep runs, in seconds. Not exposed in
# the admin UI (the *retention period* is; how often we check for expired
# jobs is just an implementation detail) — 6 hours is frequent enough for a
# single shop without adding noticeable load.
CLEANUP_INTERVAL_SEC = 6 * 60 * 60

# --- Online orders (print.tfix.in) ---
# When both are set, the server fetches orders placed on the website (even
# while the shop was closed) and adds them to the queue as unpaid jobs.
# Outbound HTTPS only — nothing on this PC is exposed to the internet.
CLOUD_URL = os.environ.get("PRINTSHOP_CLOUD_URL", "")  # e.g. https://print.tfix.in/api/shop.php
CLOUD_TOKEN = os.environ.get("PRINTSHOP_CLOUD_TOKEN", "")
CLOUD_SYNC_INTERVAL_SEC = int(os.environ.get("PRINTSHOP_CLOUD_SYNC_INTERVAL_SEC", 60))
