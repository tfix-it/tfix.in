from . import print_service
"""
Customer-facing API: upload a file, get a live quote, submit it as a print
job, and track it afterwards. Nothing in this file requires a login —
`check_rate_limit` (per-IP, admin-configurable) is the only gate on the
endpoints that cost the server real work.
"""
from pathlib import Path

from fastapi import (
    APIRouter, Depends, File, Form, HTTPException,
    UploadFile, WebSocket, WebSocketDisconnect,
)

from . import config, convert, db, pricing, printing
from .security import check_rate_limit
from .ws_hub import hub

router = APIRouter()


@router.post("/api/upload", dependencies=[Depends(check_rate_limit)])
async def upload_file(file: UploadFile = File(...)):
    settings = db.get_app_settings()
    max_bytes = settings["max_upload_mb"] * 1024 * 1024

    # Stream the upload in chunks and abort as soon as it crosses the
    # limit, rather than reading the whole thing into memory first —
    # Content-Length can't be trusted (missing or spoofed), so this is the
    # only reliable way to cap what a malicious upload can cost the server.
    CHUNK_SIZE = 1024 * 1024  # 1 MB
    chunks = []
    total = 0
    while True:
        chunk = await file.read(CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File too large — the limit is {settings['max_upload_mb']} MB.",
            )
        chunks.append(chunk)
    content = b"".join(chunks)

    try:
        saved_path = convert.save_upload(content, file.filename)
        pdf_path = convert.convert_to_pdf(saved_path)
        pages = pricing.get_page_count(str(pdf_path))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "pdf_filename": pdf_path.name,
        # For docx/pptx/images, conversion produces a separate PDF and the
        # originally-saved file (still under its own temp name) sticks
        # around on disk too — report it so /api/jobs can record it against
        # the job. None when the upload was already a PDF, since saved_path
        # and pdf_path are the same file.
        "source_filename": saved_path.name if saved_path != pdf_path else None,
        "original_filename": file.filename,
        "file_type": Path(file.filename).suffix.lower().lstrip("."),
        "pages": pages,
    }


@router.post("/api/quote")
async def quote(
    pages: int = Form(...),
    color: bool = Form(False),
    doubleside: bool = Form(False),
    pages_per_sheet: int = Form(1),
    copies: int = Form(1),
    paper_size: str = Form("A4"),
    phone: str = Form(""),
    # How many other files the customer has already queued up in this same
    # multi-file submission, ahead of the one being quoted here — so a
    # second/third file's live quote can already reflect the waived minimum
    # charge before the batch is actually submitted. 0 if unknown/omitted.
    files_ahead_today: int = Form(0),
):
    apply_min_cost = True
    if phone:
        already_today = db.count_jobs_by_phone_today(phone)
        apply_min_cost = (already_today + files_ahead_today) == 0
    try:
        total_cost, total_sheets = pricing.print_cost(
            pages, pages_per_sheet, color, doubleside, copies, paper_size,
            apply_min_cost=apply_min_cost,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "cost": round(total_cost, 2),
        "sheets": total_sheets,
        "min_cost_applied": apply_min_cost,
    }


@router.post("/api/jobs", dependencies=[Depends(check_rate_limit)])
async def create_job(
    phone: str = Form(...),
    pdf_filename: str = Form(...),
    original_filename: str = Form(...),
    file_type: str = Form(...),
    pages: int = Form(...),
    color: bool = Form(False),
    doubleside: bool = Form(False),
    duplex_edge: str = Form("long"),
    pages_per_sheet: int = Form(1),
    copies: int = Form(1),
    paper_size: str = Form("A4"),
    orientation: str = Form("portrait"),
    scale_mode: str = Form("fit"),
    pages_range: str = Form(""),
    source_filename: str = Form(""),
):
    if orientation not in ("portrait", "landscape"):
        raise HTTPException(status_code=400, detail="orientation must be 'portrait' or 'landscape'")
    if duplex_edge not in ("long", "short"):
        raise HTTPException(status_code=400, detail="duplex_edge must be 'long' or 'short'")
    if scale_mode not in ("fit", "actual"):
        raise HTTPException(status_code=400, detail="scale_mode must be 'fit' or 'actual'")

    pdf_path = config.UPLOAD_DIR / pdf_filename
    if not pdf_path.exists():
        raise HTTPException(status_code=400, detail="Upload expired — please re-upload your file")

    source_path = config.UPLOAD_DIR / source_filename if source_filename else None
    if source_path and not source_path.exists():
        source_path = None  # already gone/expired — just skip it

    daily_seq = db.count_jobs_by_phone_today(phone) + 1
    # The minimum-charge floor only applies to a phone number's first job of
    # the day — from the 2nd job onward it's waived and the job is charged
    # at the plain per-sheet rate.
    apply_min_cost = daily_seq == 1

    total_cost, total_sheets = pricing.print_cost(
        pages, pages_per_sheet, color, doubleside, copies, paper_size,
        apply_min_cost=apply_min_cost,
    )

    # Files stay under their original random UUID names on disk — we don't
    # rename them to something phone-based. A UUIDv4 name is effectively
    # unguessable, so it's safe to serve from an unauthenticated /uploads
    # route; a name built from the customer's phone number + timestamp is
    # not (both are guessable/knowable), and would turn that same route
    # into a way to pull up someone else's document. The admin dashboard
    # already shows phone number, date and original filename alongside the
    # file link, so nothing recognizable is lost — it just isn't baked into
    # the filename anymore.
    new_source_filename = source_path.name if source_path else None

    job_id = db.create_job(
        phone=phone,
        original_filename=original_filename,
        pdf_filename=pdf_filename,
        source_filename=new_source_filename,
        file_type=file_type,
        pages=pages,
        pages_range=pages_range,
        sheets=total_sheets,
        copies=copies,
        color=int(color),
        doubleside=int(doubleside),
        duplex_edge=duplex_edge,
        pages_per_sheet=pages_per_sheet,
        paper_size=paper_size,
        orientation=orientation,
        scale_mode=scale_mode,
        scale_value=None,
        auto_printed=0,
        daily_seq=daily_seq,
        cost=round(total_cost, 2),
        status="queued",
        paid=0,
        printer_id=None,
    )
    job = db.get_job(job_id)
    await hub.broadcast_job(job)

    job = maybe_auto_print(job)
    if job["status"] == "printing":
        await hub.broadcast_job(job)

    return job


def maybe_auto_print(job: dict) -> dict:
    """If the shop's auto-print rule matches this job, send it straight to
    the configured printer and mark it as printing. Never raises — a failed
    auto-print just leaves the job queued for the admin to print manually."""
    if not job.get('paid') or job.get('status') != 'queued':
        return job
    settings = db.get_auto_print_settings()
    if not settings or not settings["enabled"] or not settings["printer_name"]:
        return job

    if settings["bw_only"] and job["color"]:
        return job
    if settings["max_sheets"] is not None and job["sheets"] > settings["max_sheets"]:
        return job
    if settings["max_copies"] is not None and job["copies"] > settings["max_copies"]:
        return job

    printer_name = settings["printer_name"]
    printer_row = next(
        (p for p in db.list_printers() if p["windows_printer_name"] == printer_name),
        None,
    )
    if not printer_row or not printer_row["enabled"]:
        return job  # configured printer is missing/disabled — leave queued

    try:
        return print_service.submit(job['id'], printer_name, actor='auto', auto=True)
    except print_service.PrintBlocked:
        return db.get_job(job['id'])


@router.get("/api/jobs/{job_id}")
async def get_job(job_id: int):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/api/jobs/by-phone/{phone}")
async def jobs_by_phone_today(phone: str):
    # No auth here by design — the customer is looking up jobs tied to the
    # phone number they themselves just typed in, same as the queue screen
    # they'd see if they asked at the counter. Only today's jobs are
    # returned, and only status/cost/filename — nothing more sensitive.
    return db.list_jobs_by_phone_today(phone)


@router.websocket("/ws/customer/{job_id}")
async def ws_customer(websocket: WebSocket, job_id: int):
    await hub.customer_connect(websocket, job_id)
    try:
        while True:
            await websocket.receive_text()  # keep-alive ping from client
    except WebSocketDisconnect:
        hub.customer_disconnect(websocket, job_id)
