"""T-PRINT MCP App: the shop's print queue as an interactive dashboard inside Claude.

Built on the official MCP Python SDK 2.x (`mcp.server.mcpserver.MCPServer`) with
the MCP Apps extension (`mcp.server.apps`). The `open_print_dashboard` tool is
bound to a `ui://` HTML resource that Claude renders in a sandboxed iframe; the
dashboard then calls this server's tools itself to refresh and to print.

Transports
  http  (default)  loopback-only Streamable HTTP on 127.0.0.1:8765/mcp, guarded by
                   a bearer token. Publish ONLY this port through an HTTPS tunnel.
  stdio            for a local MCP client (e.g. Claude Desktop) launched on the shop
                   PC itself. No network listener and no token.

With TPRINT_REMOTE_ADMIN=1 the same HTTP listener also serves the full shop
admin on every path except /mcp, for https://print.tfix.in (public_server.py).

Every print, from Claude or from the dashboard, goes through the same durable
print gate (print_service.submit) as the Android/web admin and auto-print.
"""
from __future__ import annotations

import argparse
import os
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

from mcp.server.apps import Apps
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from starlette.responses import JSONResponse

from . import db, print_service, printing

DASHBOARD_URI = 'ui://tprint/dashboard.html'
DASHBOARD_HTML = (Path(__file__).resolve().parent / 'mcp_ui' / 'dashboard.html').read_text(encoding='utf-8')
STATUSES = ('queued', 'printing', 'ready', 'collected', 'all')
# Customer phone numbers and uploaded filenames are deliberately never exposed.
JOB_FIELDS = ('id', 'status', 'paid', 'cost', 'pages', 'sheets', 'copies', 'color',
              'doubleside', 'pages_per_sheet', 'paper_size', 'files_deleted', 'created_at')

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)


def printing_enabled() -> bool:
    return os.environ.get('TPRINT_MCP_ALLOW_PRINT', '0') == '1'


def remote_admin_enabled() -> bool:
    return os.environ.get('TPRINT_REMOTE_ADMIN', '0') == '1'


def admin_url() -> str | None:
    """https://print.tfix.in/ when the online admin is published, else None."""
    host = os.environ.get('TPRINT_MCP_PUBLIC_HOST', '').strip()
    return f'https://{host}/' if host and remote_admin_enabled() else None


def summary(job: dict) -> dict:
    return {key: job.get(key) for key in JOB_FIELDS}


def _check_query(status: str, limit: int) -> None:
    if status not in STATUSES:
        raise ToolError('status must be one of: ' + ', '.join(STATUSES))
    if not 1 <= limit <= 200:
        raise ToolError('limit must be 1..200')


def _printers() -> list[dict]:
    return [{**p, 'status': printing.get_printer_status(p['windows_printer_name'])}
            for p in db.list_printers()]


def dashboard_snapshot(status: str = 'queued', limit: int = 50) -> dict[str, Any]:
    _check_query(status, limit)
    every_job = db.list_jobs(None)
    counts = {s: 0 for s in STATUSES if s != 'all'}
    for job in every_job:
        counts[job['status']] = counts.get(job['status'], 0) + 1
    counts['awaiting_payment'] = sum(1 for j in every_job if j['status'] == 'queued' and not j['paid'])
    jobs = every_job if status == 'all' else [j for j in every_job if j['status'] == status]
    return {
        'status_filter': status,
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'printing_enabled': printing_enabled(),
        'admin_url': admin_url(),
        'counts': counts,
        'jobs': [summary(j) for j in jobs[:limit]],
        'total_matching': len(jobs),
        'printers': _printers(),
        'pricing': db.list_pricing(),
        'auto_print': db.get_auto_print_settings() or {},
    }


def snapshot_text(snap: dict) -> str:
    """Plain-text rendering for the model and for clients without MCP Apps support."""
    c = snap['counts']
    lines = [
        f"T-PRINT queue - showing {snap['status_filter']} ({len(snap['jobs'])} of {snap['total_matching']}).",
        f"Totals: {c.get('queued', 0)} queued ({c['awaiting_payment']} awaiting payment), "
        f"{c.get('printing', 0)} printing, {c.get('ready', 0)} ready, {c.get('collected', 0)} collected.",
        'Remote printing from Claude: ' + ('ENABLED' if snap['printing_enabled'] else 'disabled') + '.',
        'Printers: ' + (', '.join(f"{p['windows_printer_name']} ({p['status']}{'' if p['enabled'] else ', disabled'})"
                                  for p in snap['printers']) or 'none registered') + '.',
    ]
    for j in snap['jobs']:
        lines.append(
            f"- Job {j['id']}: {j['status']}, {'PAID' if j['paid'] else 'UNPAID'}, Rs {j['cost']:g}, "
            f"{j['sheets']} sheet(s) x {j['copies']} cop{'y' if j['copies'] == 1 else 'ies'}, "
            f"{'colour' if j['color'] else 'B&W'} {j['paper_size']}"
            f"{', double-sided' if j['doubleside'] else ''}{', FILES DELETED' if j['files_deleted'] else ''}")
    return '\n'.join(lines)


# ---------------------------------------------------------------- MCP App (UI)
apps = Apps()


@apps.tool(
    resource_uri=DASHBOARD_URI,
    name='open_print_dashboard',
    title='Open T-PRINT dashboard',
    description=(
        'Show the live T-PRINT print-shop dashboard: job queue, payment state, printer status '
        'and pricing. Staff can refresh it and send paid jobs to a printer from inside the '
        "dashboard. status: queued (default), printing, ready, collected or all. limit: 1..200."),
    annotations=READ_ONLY,
)
def open_print_dashboard(status: str = 'queued', limit: int = 50) -> CallToolResult:
    snap = dashboard_snapshot(status, limit)
    return CallToolResult(content=[TextContent(type='text', text=snapshot_text(snap))],
                          structured_content=snap)


apps.add_html_resource(
    DASHBOARD_URI,
    DASHBOARD_HTML,
    name='tprint-dashboard',
    title='T-PRINT dashboard',
    description='Interactive print queue for the shop owner.',
    prefers_border=True,
)

mcp = MCPServer(
    'T-PRINT',
    title='T-PRINT · T FIX Auto Print',
    version='2.0.0',
    instructions=(
        'T-PRINT manages a print shop queue. Use open_print_dashboard to show the interactive '
        'queue. Never claim a job is paid: only shop staff can confirm payment. Before calling '
        'print_paid_job, get explicit confirmation of the job ID and printer from the user.'),
    extensions=[apps],
)


# ---------------------------------------------------------------- plain tools
@mcp.tool(annotations=READ_ONLY)
def list_print_jobs(status: str = 'queued', limit: int = 50) -> list[dict]:
    """Read up to 200 jobs; status may be queued, printing, ready, collected or all."""
    _check_query(status, limit)
    return [summary(j) for j in db.list_jobs(None if status == 'all' else status)[:limit]]


@mcp.tool(annotations=READ_ONLY)
def list_printers() -> list[dict]:
    """Read configured Windows printers and their reported status."""
    return _printers()


@mcp.tool(annotations=READ_ONLY)
def get_pricing() -> list[dict]:
    """Read current printing prices in INR."""
    return db.list_pricing()


@mcp.tool(annotations=READ_ONLY)
def get_auto_print_settings() -> dict:
    """Read auto-print rules. Auto-print requires staff-confirmed payment."""
    return db.get_auto_print_settings() or {}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True,
                                      idempotentHint=True, openWorldHint=True))
def print_paid_job(job_id: int, printer_name: str, confirm: bool = False) -> dict:
    """Physically print one paid, queued job. Obtain the user's explicit confirmation
    of job ID and printer before setting confirm=true. Cannot confirm payments or reprint."""
    if not printing_enabled():
        raise ToolError('MCP printing disabled. Shop owner must enable it locally.')
    if not confirm:
        raise ToolError('Explicit confirmation required.')
    try:
        job = print_service.submit(job_id, printer_name, actor='claude_mcp')
    except print_service.PrintBlocked as exc:
        # ToolError (not a bare exception) so the gate's reason reaches Claude and the dashboard.
        raise ToolError(str(exc)) from exc
    return {'job': summary(job), 'result': 'submitted; physical completion is not verified'}


# ---------------------------------------------------------------- HTTP transport
class BearerGuard:
    """Reject every request that does not carry the exact bearer token."""

    def __init__(self, app, token: str):
        self.app = app
        self.expected = 'Bearer ' + token

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'websocket':
            await send({'type': 'websocket.close', 'code': 1008})
            return
        if scope['type'] == 'http':
            headers = dict(scope['headers'])
            provided = headers.get(b'authorization', b'').decode('latin1')
            if not secrets.compare_digest(provided, self.expected):
                await JSONResponse({'error': 'Unauthorized'}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_http_app(token: str, public_host: str = '', remote_admin: bool = False):
    """The whole 127.0.0.1:8765 listener: /mcp for Claude, plus the online shop
    admin on every other path when remote_admin is on (see public_server.py)."""
    from contextlib import asynccontextmanager
    from .public_server import HostOriginGuard, PublicApp

    if len(token) < 32:
        raise RuntimeError('Set TPRINT_MCP_TOKEN to a random secret of at least 32 characters.')
    hosts = ['127.0.0.1', 'localhost', '127.0.0.1:*', 'localhost:*']
    public_host = public_host.strip()
    if public_host:
        if any(c in public_host for c in '/:* '):
            raise RuntimeError('TPRINT_MCP_PUBLIC_HOST must be a hostname only.')
        hosts.append(public_host)
    security = TransportSecuritySettings(
        allowed_hosts=hosts,
        allowed_origins=['https://' + public_host] if public_host else [])
    starlette_app = mcp.streamable_http_app(stateless_http=True, json_response=True,
                                            transport_security=security)
    session_manager = mcp.session_manager
    admin_app = None
    if remote_admin:
        from . import job_watch
        from .public_server import build_admin_app
        admin_app = HostOriginGuard(build_admin_app(), public_host)

    @asynccontextmanager
    async def lifespan():
        async with session_manager.run():
            if remote_admin:
                job_watch.start_job_watch()
            try:
                yield
            finally:
                if remote_admin:
                    job_watch.stop_job_watch()

    return PublicApp(BearerGuard(starlette_app, token), admin_app, lifespan)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog='python -m app.mcp_server', description=__doc__.splitlines()[0])
    parser.add_argument('--transport', choices=('http', 'stdio'), default='http')
    parser.add_argument('--port', type=int, default=int(os.environ.get('TPRINT_MCP_PORT', '8765')))
    args = parser.parse_args(argv)

    if not db.config.DB_PATH.exists():
        raise SystemExit('Start the shop server once before starting MCP.')
    print_service.initialize()

    if args.transport == 'stdio':
        mcp.run('stdio')  # stdout carries the protocol: never print() in this mode
        return

    import uvicorn
    app = create_http_app(os.environ.get('TPRINT_MCP_TOKEN', ''),
                          os.environ.get('TPRINT_MCP_PUBLIC_HOST', ''),
                          remote_admin=remote_admin_enabled())
    if remote_admin_enabled():
        print(f"[tprint] Online admin enabled at {admin_url() or 'http://127.0.0.1:%d/' % args.port}")
    # cloudflared connects from 127.0.0.1: trust its X-Forwarded-For so the
    # admin login rate limit sees each visitor's real address.
    uvicorn.run(app, host='127.0.0.1', port=args.port, proxy_headers=True,
                forwarded_allow_ips='127.0.0.1')


if __name__ == '__main__':
    main()
