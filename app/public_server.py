"""
The internet-facing side of TPrint, served at https://print.tfix.in through a
Cloudflare named tunnel to 127.0.0.1:8765 on the shop PC.

  /mcp        Claude's connector (bearer token). See mcp_server.py.
  everything  The full shop admin: the same page (static/admin/index.html) and
  else        the same API (routes_admin.py) as the LAN admin, plus guarded
              downloads of job files. Only when TPRINT_REMOTE_ADMIN=1.

Never published: the customer upload pages and API (routes_customer.py), and
the LAN server's /uploads mount. Customers keep using the shop WiFi site.

Security layers for the admin, outermost first:
  1. Cloudflare Access login (email one-time code) in front of print.tfix.in,
     enforced again by cloudflared itself (originRequest.access.required) so
     nothing reaches this process without a valid Access token. /mcp bypasses
     Access and relies on the bearer token instead. See START_HERE.md.
  2. This module: Host allow-list, Origin check on state-changing requests and
     WebSocket handshakes, no API docs, no-store caching.
  3. The shop's admin password and session cookie (Secure, HttpOnly,
     SameSite=Lax), with the existing per-IP login rate limit. uvicorn trusts
     X-Forwarded-For from cloudflared on 127.0.0.1, so the limit sees real
     client addresses.
"""
from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from starlette.responses import PlainTextResponse

from . import config, routes_admin
from .security import HSTSMiddleware, require_admin

ADMIN_PAGE = Path(__file__).resolve().parent.parent / "static" / "admin" / "index.html"
LOCAL_HOSTS = ("127.0.0.1", "localhost")
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
NO_STORE = {"Cache-Control": "no-store"}


def build_admin_app() -> FastAPI:
    app = FastAPI(title="TPrint admin", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(HSTSMiddleware)
    app.include_router(routes_admin.router)

    @app.get("/", include_in_schema=False)
    def admin_page():
        return FileResponse(ADMIN_PAGE, media_type="text/html", headers=NO_STORE)

    @app.get("/admin", include_in_schema=False)
    @app.get("/admin/", include_in_schema=False)
    def admin_redirect():
        return RedirectResponse("/", status_code=308)

    @app.get("/uploads/{name}", dependencies=[Depends(require_admin)], include_in_schema=False)
    def job_file(name: str):
        # The LAN server mounts /uploads unauthenticated; online, every file
        # needs an admin session and must resolve inside the uploads folder.
        root = config.UPLOAD_DIR.resolve()
        path = (root / name).resolve()
        if not path.is_relative_to(root) or path.parent != root or not path.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        is_pdf = path.suffix.lower() == ".pdf"
        return FileResponse(
            path,
            media_type="application/pdf" if is_pdf else None,
            content_disposition_type="inline" if is_pdf else "attachment",
            filename=path.name,
            headers={**NO_STORE, "X-Content-Type-Options": "nosniff"},
        )

    return app


class HostOriginGuard:
    """Reject requests for unexpected Host names (DNS rebinding) and
    cross-site Origins on anything that changes state or opens a WebSocket."""

    def __init__(self, app, public_host: str = ""):
        self.app = app
        self.hosts = set(LOCAL_HOSTS) | ({public_host} if public_host else set())
        self.public_origin = f"https://{public_host}" if public_host else None

    def _origin_ok(self, origin: str) -> bool:
        if origin == self.public_origin:
            return True
        parts = urlsplit(origin)
        return parts.scheme in ("http", "https") and parts.hostname in LOCAL_HOSTS

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope["headers"]}
        host = urlsplit("//" + headers.get("host", "")).hostname or ""
        problem = None
        if host not in self.hosts:
            problem = (400, "Invalid host")
        elif scope["type"] == "websocket" or scope.get("method") not in SAFE_METHODS:
            origin = headers.get("origin")
            if origin is not None and not self._origin_ok(origin):
                problem = (403, "Cross-site request refused")
        if problem is None:
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await PlainTextResponse(problem[1], status_code=problem[0])(scope, receive, send)


class PublicApp:
    """One ASGI app for the whole listener: /mcp goes to the MCP connector,
    everything else to the admin (or to the connector's 401 when the online
    admin is off). Runs the shared lifespan itself."""

    def __init__(self, mcp_app, admin_app, lifespan: Callable[[], AbstractAsyncContextManager]):
        self.mcp_app = mcp_app
        self.admin_app = admin_app
        self.lifespan = lifespan

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self._run_lifespan(receive, send)
        path = scope.get("path", "")
        if self.admin_app is None or path == "/mcp" or path.startswith("/mcp/"):
            return await self.mcp_app(scope, receive, send)
        return await self.admin_app(scope, receive, send)

    async def _run_lifespan(self, receive, send):
        await receive()  # lifespan.startup
        context = self.lifespan()
        try:
            await context.__aenter__()
        except Exception as exc:
            await send({"type": "lifespan.startup.failed", "message": str(exc)})
            return
        await send({"type": "lifespan.startup.complete"})
        await receive()  # lifespan.shutdown
        await context.__aexit__(None, None, None)
        await send({"type": "lifespan.shutdown.complete"})
