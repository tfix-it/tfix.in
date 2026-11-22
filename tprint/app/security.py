"""
Security and abuse-control primitives shared by routes_customer.py and
routes_admin.py:
  - HSTSMiddleware and the plain-HTTP -> HTTPS redirect listener
  - admin session helpers (require_admin)
  - per-IP rate limiting, for customer endpoints and for admin login
"""
import threading
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware

from . import config, db


class HSTSMiddleware(BaseHTTPMiddleware):
    """Tells the browser to remember, for future visits, that this host
    should only ever be reached over HTTPS — so even a manually-typed
    "http://printatshop.local" or an old bookmark gets upgraded client-side
    on the *next* visit, without waiting on a round trip to our redirect
    listener. Only meaningful once a visit has happened over HTTPS at least
    once (the first request always needs the redirect listener below)."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response


# ---------------- plain-HTTP -> HTTPS redirect ----------------
# uvicorn's TLS listener can't also answer plain HTTP, so this is a second,
# minimal listener (stdlib only, no extra deps) whose only job is to
# 301-redirect any http:// request over to the https:// site. Runs in a
# background thread for the life of the process.
class _RedirectHandler(BaseHTTPRequestHandler):
    def _redirect(self):
        host = (self.headers.get("Host") or f"{config.MDNS_HOSTNAME}.local").split(":")[0]
        # Omit the port when it's 443 — that's HTTPS's default, so a bare
        # "https://host/..." already means "port 443" and including it
        # explicitly would just clutter the address bar.
        port_suffix = "" if config.PORT == 443 else f":{config.PORT}"
        target = f"https://{host}{port_suffix}{self.path}"
        self.send_response(301)
        self.send_header("Location", target)
        self.end_headers()

    do_GET = _redirect
    do_HEAD = _redirect
    do_POST = _redirect

    def log_message(self, fmt, *args):
        pass  # keep this quiet — it only ever redirects, nothing to debug


_http_redirect_server: Optional[ThreadingHTTPServer] = None


def start_http_redirect_server():
    global _http_redirect_server
    _http_redirect_server = ThreadingHTTPServer(
        (config.HOST, config.HTTP_REDIRECT_PORT), _RedirectHandler
    )
    thread = threading.Thread(target=_http_redirect_server.serve_forever, daemon=True)
    thread.start()
    print(f"[http-redirect] http://*:{config.HTTP_REDIRECT_PORT} -> https://*:{config.PORT}")


def stop_http_redirect_server():
    global _http_redirect_server
    if _http_redirect_server:
        _http_redirect_server.shutdown()
        _http_redirect_server = None


# ---------------- admin sessions ----------------
# DB-backed (see db.admin_sessions) rather than an in-memory set, so a
# server restart doesn't silently log the admin out mid-shift and this
# isn't tied to a single process.
ADMIN_SESSION_TTL_SEC = 60 * 60 * 12  # matches the cookie's max_age in routes_admin.py


def require_admin(request: Request):
    token = request.cookies.get(config.SESSION_COOKIE_NAME)
    if not db.get_valid_session(token):
        raise HTTPException(status_code=401, detail="Not authenticated")


# ---------------- per-IP rate limiting (customer endpoints) ----------------
# Simple in-memory sliding-window limiter shared by the customer-facing
# upload/submit endpoints, keyed by client IP. Fine for a single shop LAN —
# nothing to install, no persistence needed. Limits are read from the DB on
# every call so admin changes apply immediately, no restart required.
_rate_buckets: dict[str, deque] = defaultdict(deque)


def check_rate_limit(request: Request):
    settings = db.get_app_settings()
    window = settings["rate_limit_window_sec"]
    max_requests = settings["rate_limit_max_requests"]
    if max_requests <= 0:
        return  # 0 (or less) disables the limiter entirely

    ip = request.client.host if request.client else "unknown"
    bucket = _rate_buckets[ip]
    now_t = time.monotonic()
    while bucket and now_t - bucket[0] > window:
        bucket.popleft()

    if len(bucket) >= max_requests:
        raise HTTPException(
            status_code=429,
            detail="Too many uploads from this device — please wait a bit and try again.",
        )
    bucket.append(now_t)


# ---------------- admin login rate limiting ----------------
# Separate, deliberately NOT tied to the admin-configurable app_settings
# above — those exist to tune abuse limits on customer-facing endpoints,
# but the login limiter is a security control and shouldn't be loosenable
# from the same panel it's protecting. Fixed at 5 attempts / 15 minutes
# per IP, regardless of username or whether the attempt succeeded.
LOGIN_RATE_LIMIT_WINDOW_SEC = 15 * 60
LOGIN_RATE_LIMIT_MAX_ATTEMPTS = 5

_login_attempt_buckets: dict[str, deque] = defaultdict(deque)


def check_login_rate_limit(request: Request):
    ip = request.client.host if request.client else "unknown"
    bucket = _login_attempt_buckets[ip]
    now_t = time.monotonic()
    while bucket and now_t - bucket[0] > LOGIN_RATE_LIMIT_WINDOW_SEC:
        bucket.popleft()

    if len(bucket) >= LOGIN_RATE_LIMIT_MAX_ATTEMPTS:
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts — please wait 15 minutes and try again.",
        )
    bucket.append(now_t)
