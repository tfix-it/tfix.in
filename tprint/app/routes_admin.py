from . import print_service
"""
Admin-only API: authentication, job management, printers, pricing, and
system settings. Every route except /api/admin/login depends on
require_admin (a valid session cookie). The websocket does the same check
by hand, since FastAPI's Depends()-based auth doesn't run before a
websocket handshake is accepted.
"""
import secrets
from typing import Optional

from fastapi import (
    APIRouter, Depends, Form, HTTPException, Request,
    WebSocket, WebSocketDisconnect,
)
from fastapi.responses import JSONResponse

from . import config, db, printing
from .cleanup import run_unpaid_cleanup
from .security import ADMIN_SESSION_TTL_SEC, check_login_rate_limit, require_admin
from .ws_hub import hub

router = APIRouter()


@router.post("/api/admin/login", dependencies=[Depends(check_login_rate_limit)])
async def admin_login(username: str = Form(...), password: str = Form(...)):
    # Both checks always run (not short-circuited) and both use
    # timing-safe comparison, so a wrong guess can't be timed to learn
    # whether it was the username or the password that was wrong.
    user_ok = secrets.compare_digest(username, config.ADMIN_USERNAME)
    pass_ok = db.verify_admin_password(password)
    if not (user_ok and pass_ok):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = secrets.token_urlsafe(32)
    db.create_session(token, ADMIN_SESSION_TTL_SEC)
    response = JSONResponse({"status": "success", "session_token": token})
    response.set_cookie(
        config.SESSION_COOKIE_NAME, token,
        httponly=True, samesite="lax", secure=config.FORCE_SECURE_COOKIE,
        max_age=ADMIN_SESSION_TTL_SEC,
    )
    return response


@router.post("/api/admin/logout")
async def admin_logout(request: Request):
    token = request.cookies.get(config.SESSION_COOKIE_NAME)
    if token:
        db.delete_session(token)
    response = JSONResponse({"ok": True})
    response.delete_cookie(config.SESSION_COOKIE_NAME)
    return response


@router.post(
    "/api/admin/change-password",
    dependencies=[Depends(require_admin), Depends(check_login_rate_limit)],
)
async def admin_change_password(
    current_password: str = Form(...),
    new_password: str = Form(...),
):
    if not db.verify_admin_password(current_password):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters")

    db.set_admin_password(new_password)
    # Every existing session — including this one — is signed out, so
    # anyone still holding an old session cookie has to prove they know
    # the new password too.
    db.delete_all_sessions()
    response = JSONResponse({"ok": True})
    response.delete_cookie(config.SESSION_COOKIE_NAME)
    return response


@router.get("/api/admin/jobs", dependencies=[Depends(require_admin)])
async def admin_list_jobs(status: Optional[str] = None):
    return db.list_jobs(status)


@router.post("/api/admin/jobs/{job_id}/status", dependencies=[Depends(require_admin)])
async def admin_update_status(job_id: int, status: str = Form(...)):
    job = db.update_job(job_id, status=status)
    await hub.broadcast_job(job)
    return job


@router.post("/api/admin/jobs/{job_id}/paid", dependencies=[Depends(require_admin)])
async def admin_mark_paid(job_id: int, paid: bool = Form(True)):
    try:
        job = print_service.confirm_payment(job_id, paid)
    except print_service.PrintBlocked as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if paid:
        from .routes_customer import maybe_auto_print
        job = maybe_auto_print(job)
    await hub.broadcast_job(job)
    return job


@router.post("/api/admin/jobs/{job_id}/print", dependencies=[Depends(require_admin)])
async def admin_print_job(job_id: int, printer_name: str = Form(...)):
    try:
        job = print_service.submit(job_id, printer_name)
    except print_service.PrintBlocked as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    await hub.broadcast_job(job)
    return job


@router.get("/api/admin/printers", dependencies=[Depends(require_admin)])
async def admin_list_printers():
    printers = db.list_printers()
    for p in printers:
        p["live_status"] = printing.get_printer_status(p["windows_printer_name"])
    return printers


@router.post("/api/admin/printers/{printer_id}/enabled", dependencies=[Depends(require_admin)])
async def admin_toggle_printer(printer_id: int, enabled: bool = Form(...)):
    db.set_printer_enabled(printer_id, enabled)
    return {"ok": True}


@router.get("/api/admin/pricing", dependencies=[Depends(require_admin)])
async def admin_get_pricing():
    return db.list_pricing()


@router.post("/api/admin/pricing", dependencies=[Depends(require_admin)])
async def admin_update_pricing(
    paper_size: str = Form(...),
    color: bool = Form(...),
    singleside_cost: float = Form(...),
    doubleside_cost: float = Form(...),
    min_cost: float = Form(...),
):
    db.update_pricing(paper_size, color, singleside_cost, doubleside_cost, min_cost)
    return {"ok": True}


@router.get("/api/admin/auto-print", dependencies=[Depends(require_admin)])
async def admin_get_auto_print():
    return db.get_auto_print_settings()


@router.post("/api/admin/auto-print", dependencies=[Depends(require_admin)])
async def admin_update_auto_print(
    enabled: bool = Form(...),
    printer_name: str = Form(""),
    bw_only: bool = Form(True),
    max_sheets: Optional[int] = Form(None),
    max_copies: Optional[int] = Form(None),
):
    db.update_auto_print_settings(
        enabled, printer_name or None, bw_only, max_sheets, max_copies
    )
    return db.get_auto_print_settings()


@router.get("/api/admin/settings", dependencies=[Depends(require_admin)])
async def admin_get_settings():
    return db.get_app_settings()


@router.post("/api/admin/settings", dependencies=[Depends(require_admin)])
async def admin_update_settings(
    max_upload_mb: int = Form(...),
    unpaid_cleanup_days: int = Form(...),
    rate_limit_window_sec: int = Form(...),
    rate_limit_max_requests: int = Form(...),
):
    if max_upload_mb < 1:
        raise HTTPException(status_code=400, detail="Max upload size must be at least 1 MB")
    if unpaid_cleanup_days < 1:
        raise HTTPException(status_code=400, detail="Cleanup period must be at least 1 day")
    if rate_limit_window_sec < 1:
        raise HTTPException(status_code=400, detail="Rate limit window must be at least 1 second")
    if rate_limit_max_requests < 0:
        raise HTTPException(status_code=400, detail="Rate limit max requests can't be negative")

    return db.update_app_settings(
        max_upload_mb=max_upload_mb,
        unpaid_cleanup_days=unpaid_cleanup_days,
        rate_limit_window_sec=rate_limit_window_sec,
        rate_limit_max_requests=rate_limit_max_requests,
    )


@router.post("/api/admin/settings/run-cleanup-now", dependencies=[Depends(require_admin)])
async def admin_run_cleanup_now():
    """Manual trigger, mostly useful right after lowering the retention
    period, so the admin doesn't have to wait for the next scheduled sweep."""
    run_unpaid_cleanup()
    return {"ok": True}


def _cloud_client():
    from . import cloud_sync

    if not cloud_sync.is_configured():
        raise HTTPException(status_code=400, detail="Online orders are not set up on this PC (PRINTSHOP_CLOUD_URL / TOKEN).")
    return cloud_sync, cloud_sync.CloudClient(cloud_sync.cloud_url(), cloud_sync.cloud_token(), timeout=20)


def _cloud_error(e) -> HTTPException:
    msg = str(e)
    if "HTTP 400" in msg:
        return HTTPException(status_code=400, detail="Not a valid 10-digit mobile number or amount.")
    if "HTTP 409" in msg:
        return HTTPException(status_code=409, detail="Not enough rewards for this number.")
    return HTTPException(status_code=502, detail=f"Could not reach print.tfix.in: {msg}")


@router.get("/api/admin/cloud/rewards", dependencies=[Depends(require_admin)])
async def admin_customer_rewards(phone: str):
    """CLUTCH Rewards of an online customer (mobile number): ₹5 per ₹100 of collected prints."""
    import asyncio

    cloud_sync, client = _cloud_client()
    try:
        return await asyncio.to_thread(client.rewards, phone.strip())
    except cloud_sync.CloudError as e:
        raise _cloud_error(e)


@router.post("/api/admin/cloud/redeem", dependencies=[Depends(require_admin)])
async def admin_redeem_rewards(phone: str = Form(...), amount: float = Form(...), note: str = Form("")):
    """Use a customer's CLUTCH Rewards at the counter (take this much off their bill)."""
    import asyncio

    if amount <= 0 or amount > 100000:
        raise HTTPException(status_code=400, detail="Enter an amount in rupees.")
    cloud_sync, client = _cloud_client()
    try:
        return await asyncio.to_thread(client.redeem, phone.strip(), round(amount, 2), note.strip()[:100])
    except cloud_sync.CloudError as e:
        raise _cloud_error(e)


REPAIR_STATUSES = ("requested", "received", "repairing", "waiting_parts", "ready", "collected", "cancelled")


@router.get("/api/admin/cloud/repairs", dependencies=[Depends(require_admin)])
async def admin_repairs(all: int = 0):
    """Repair bookings from repair.tfix.in, with each customer's Phone Doctor report."""
    import asyncio

    cloud_sync, client = _cloud_client()
    try:
        return {"repairs": await asyncio.to_thread(client.repairs, bool(all))}
    except cloud_sync.CloudError as e:
        raise _cloud_error(e)


@router.post("/api/admin/cloud/repairs/{repair_id}", dependencies=[Depends(require_admin)])
async def admin_repair_update(
    repair_id: int,
    status: str = Form(...),
    quote: str = Form(""),
    final_amount: str = Form(""),
    paid: int = Form(0),
    note: str = Form(""),
):
    """Update a repair booking. Marking it collected (with final amount, paid) adds CLUTCH Rewards."""
    import asyncio

    if status not in REPAIR_STATUSES:
        raise HTTPException(status_code=400, detail="Unknown repair status.")

    def money(v: str):
        v = (v or "").strip()
        if not v:
            return None
        try:
            x = round(float(v), 2)
        except ValueError:
            raise HTTPException(status_code=400, detail="Enter amounts in rupees.")
        if x < 0 or x > 1000000:
            raise HTTPException(status_code=400, detail="Enter amounts in rupees.")
        return x

    fields = {"status": status, "quote": money(quote), "final_amount": money(final_amount),
              "paid": bool(paid), "note": note.strip()[:300]}
    cloud_sync, client = _cloud_client()
    try:
        return await asyncio.to_thread(client.repair_update, repair_id, **fields)
    except cloud_sync.CloudError as e:
        msg = str(e)
        if "HTTP 400" in msg:
            raise HTTPException(status_code=400, detail="Check the amounts: collected needs a final amount and paid.")
        if "HTTP 409" in msg:
            raise HTTPException(status_code=409, detail="This repair is already collected.")
        if "HTTP 404" in msg:
            raise HTTPException(status_code=404, detail="Repair not found.")
        raise _cloud_error(e)


@router.websocket("/ws/admin")
async def ws_admin(websocket: WebSocket):
    # Same session check as require_admin, done manually: FastAPI's
    # Depends()-based auth doesn't run before a WebSocket handshake is
    # accepted, so an unauthenticated caller could otherwise watch every
    # job (files, phone numbers, costs) come through live.
    token = websocket.cookies.get(config.SESSION_COOKIE_NAME)
    if not db.get_valid_session(token):
        await websocket.close(code=1008)  # policy violation
        return

    await hub.admin_connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        hub.admin_disconnect(websocket)
