"""
Cross-process live updates.

The shop LAN server (app.main) and the public listener (app.mcp_server, which
serves Claude and the online admin at print.tfix.in) are separate processes,
each with its own WebSocket hub. Neither sees the other's in-memory
broadcasts, so each runs this watcher: every few seconds it reads the jobs
whose `updated_at` moved since the last look and pushes them through its own
hub. That way a customer upload appears live on print.tfix.in, and a job
printed or marked paid online appears live on the shop's admin page and on
the customer's status screen.

A job its own process already broadcast may be sent a second time; admin and
customer pages treat updates as upserts, so that is harmless.
"""
import asyncio
from typing import Optional

from . import db
from .ws_hub import hub

POLL_INTERVAL_SEC = 3.0

_task: Optional[asyncio.Task] = None


def changed_since(cursor: str) -> list[dict]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE updated_at > ? ORDER BY updated_at", (cursor,)
        ).fetchall()
        return [dict(r) for r in rows]


async def _watch(interval: float):
    cursor = db.now()
    while True:
        await asyncio.sleep(interval)
        try:
            jobs = changed_since(cursor)
        except Exception as e:  # database briefly locked, etc.: try again next tick
            print(f"[job-watch] {e}")
            continue
        for job in jobs:
            cursor = max(cursor, job["updated_at"])
            await hub.broadcast_job(job)


def start_job_watch(interval: float = POLL_INTERVAL_SEC):
    global _task
    _task = asyncio.create_task(_watch(interval))


def stop_job_watch():
    global _task
    if _task:
        _task.cancel()
        _task = None
