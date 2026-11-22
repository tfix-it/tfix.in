"""
Websocket broadcast hub, shared by routes_customer.py and routes_admin.py.

A single Hub instance is imported by both routers so that one
`hub.broadcast_job(...)` call (fired whenever a job is created or updated)
reaches both audiences at once: the admin dashboard, which watches every
job, and whichever customer device is watching that one specific job's
status page.
"""
from fastapi import WebSocket

# Fields the public (no-login) job lookups may return. Phone numbers and
# on-disk filenames are left out: job IDs are sequential, so anyone could
# otherwise walk /api/jobs/1, /2, ... and collect every customer's phone
# number and a direct /uploads/ link to their document.
PUBLIC_JOB_FIELDS = (
    "id", "status", "paid", "cost", "sheets", "copies", "color", "doubleside",
    "paper_size", "daily_seq", "created_at", "updated_at",
)


def public_job(job: dict) -> dict:
    return {k: job[k] for k in PUBLIC_JOB_FIELDS if k in job}


class Hub:
    def __init__(self):
        self.admin_sockets: list[WebSocket] = []
        self.customer_sockets: dict[int, list[WebSocket]] = {}

    async def admin_connect(self, ws: WebSocket):
        await ws.accept()
        self.admin_sockets.append(ws)

    def admin_disconnect(self, ws: WebSocket):
        if ws in self.admin_sockets:
            self.admin_sockets.remove(ws)

    async def customer_connect(self, ws: WebSocket, job_id: int):
        await ws.accept()
        self.customer_sockets.setdefault(job_id, []).append(ws)

    def customer_disconnect(self, ws: WebSocket, job_id: int):
        sockets = self.customer_sockets.get(job_id, [])
        if ws in sockets:
            sockets.remove(ws)

    async def broadcast_job(self, job: dict):
        dead = []
        for ws in self.admin_sockets:
            try:
                await ws.send_json({"type": "job_update", "job": job})
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.admin_disconnect(ws)

        for ws in self.customer_sockets.get(job["id"], []):
            try:
                await ws.send_json({"type": "job_update", "job": public_job(job)})
            except Exception:
                pass


hub = Hub()
