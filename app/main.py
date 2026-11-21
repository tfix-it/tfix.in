"""
App entrypoint. Everything here is wiring: build the FastAPI app, attach
security middleware, mount the customer/admin routers and the static
frontends, and start/stop the background services (mDNS advertising,
Windows printer discovery, the HTTP->HTTPS redirect listener, and the
unpaid-job cleanup sweep) around the app's lifecycle.

The actual route handlers live in routes_customer.py and routes_admin.py;
shared infrastructure lives in security.py, ws_hub.py and cleanup.py.
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import cleanup, config, db, job_watch, mdns_service, printing, routes_admin, routes_customer
from .security import HSTSMiddleware, start_http_redirect_server, stop_http_redirect_server

app = FastAPI(title="Shop Print Server")
app.add_middleware(HSTSMiddleware)

app.include_router(routes_customer.router)
app.include_router(routes_admin.router)


# ---------------- startup / shutdown ----------------
@app.on_event("startup")
def on_startup():
    db.init_db()
    from .print_service import initialize
    initialize()
    if config.ENABLE_HTTP_REDIRECT:
        try:
            start_http_redirect_server()
        except Exception as e:
            # Most likely cause: something else is already bound to port 80
            # (e.g. another web server, or the app already running once).
            # Non-fatal — the HTTPS site itself still works fine either way.
            print(f"[http-redirect] Could not start on port {config.HTTP_REDIRECT_PORT}: {e}")
    try:
        mdns_service.start_mdns()
    except Exception as e:
        print(f"[mDNS] Could not start advertising: {e}")
    try:
        for name in printing.list_windows_printers():
            db.upsert_printer(name, name)
    except Exception as e:
        print(f"[printers] Could not enumerate Windows printers: {e}")

    cleanup.start_cleanup_loop()
    # Live updates for changes made by the separate Claude/print.tfix.in process.
    job_watch.start_job_watch()


@app.on_event("shutdown")
def on_shutdown():
    mdns_service.stop_mdns()
    stop_http_redirect_server()
    cleanup.stop_cleanup_loop()
    job_watch.stop_job_watch()


# ================= STATIC FRONTENDS =================
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/uploads", StaticFiles(directory=config.UPLOAD_DIR), name="uploads")
app.mount("/admin", StaticFiles(directory=STATIC_DIR / "admin", html=True), name="admin")
app.mount("/", StaticFiles(directory=STATIC_DIR / "customer", html=True), name="customer")


# ================= ENTRYPOINT (HTTPS) =================
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=config.HOST,
        port=config.PORT,
        ssl_keyfile=config.SSL_KEY_PATH,
        ssl_certfile=config.SSL_CERT_PATH,
    )
