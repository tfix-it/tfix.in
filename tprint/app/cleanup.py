"""
Background sweep that deletes on-disk PDF/source files for jobs still
unpaid after the admin-configured retention window. The job row itself is
kept (marked files_deleted) so it still shows up in job history.

main.py starts/stops the loop around the app's lifecycle; routes_admin.py
calls run_unpaid_cleanup() directly for the dashboard's manual "run now"
button.
"""
import asyncio
from typing import Optional

from . import config, db

_cleanup_task: Optional[asyncio.Task] = None


def run_unpaid_cleanup():
    settings = db.get_app_settings()
    days = settings["unpaid_cleanup_days"]
    stale_jobs = db.list_unpaid_jobs_for_cleanup(days)

    for job in stale_jobs:
        for fname in (job["pdf_filename"], job["source_filename"]):
            if not fname:
                continue
            path = config.UPLOAD_DIR / fname
            try:
                path.unlink(missing_ok=True)
            except OSError as e:
                print(f"[cleanup] could not delete {fname}: {e}")
        db.mark_job_files_deleted(job["id"])

    if stale_jobs:
        print(f"[cleanup] removed files for {len(stale_jobs)} unpaid job(s)")


async def _cleanup_loop():
    while True:
        try:
            run_unpaid_cleanup()
        except Exception as e:
            print(f"[cleanup] error: {e}")
        await asyncio.sleep(config.CLEANUP_INTERVAL_SEC)


def start_cleanup_loop():
    global _cleanup_task
    _cleanup_task = asyncio.create_task(_cleanup_loop())


def stop_cleanup_loop():
    global _cleanup_task
    if _cleanup_task:
        _cleanup_task.cancel()
        _cleanup_task = None
