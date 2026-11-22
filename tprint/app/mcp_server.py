"""Separate loopback-only MCP listener. Publish only this port through HTTPS."""
import os
import secrets
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.responses import JSONResponse
from . import db, printing, print_service

TOKEN = os.environ.get('TPRINT_MCP_TOKEN', '')
if len(TOKEN) < 32:
    raise RuntimeError('Set TPRINT_MCP_TOKEN to a random secret of at least 32 characters.')
hosts = ['127.0.0.1', 'localhost', '127.0.0.1:*', 'localhost:*']
public_host = os.environ.get('TPRINT_MCP_PUBLIC_HOST', '').strip()
if public_host:
    if any(c in public_host for c in '/:* '):
        raise RuntimeError('TPRINT_MCP_PUBLIC_HOST must be a hostname only.')
    hosts.append(public_host)
mcp = FastMCP('TPrint', stateless_http=True, json_response=True,
    transport_security=TransportSecuritySettings(allowed_hosts=hosts,
        allowed_origins=['https://' + public_host] if public_host else []))
read = ToolAnnotations(readOnlyHint=True, destructiveHint=False)

def summary(job):
    # Customer phone numbers and uploaded filenames are not needed by the model.
    fields = ('id','status','paid','cost','pages','sheets','copies','color','paper_size','created_at')
    return {key: job[key] for key in fields}

@mcp.tool(annotations=read)
def list_print_jobs(status: str = 'queued', limit: int = 50) -> list[dict]:
    """Read up to 200 jobs; status may be queued, printing, ready, collected or all."""
    if status not in ('queued','printing','ready','collected','all'):
        raise ValueError('Unsupported status')
    if not 1 <= limit <= 200:
        raise ValueError('limit must be 1..200')
    return [summary(j) for j in db.list_jobs(None if status == 'all' else status)[:limit]]

@mcp.tool(annotations=read)
def list_printers() -> list[dict]:
    """Read configured Windows printers and their reported status."""
    return [{**p, 'status': printing.get_printer_status(p['windows_printer_name'])} for p in db.list_printers()]

@mcp.tool(annotations=read)
def get_pricing() -> list[dict]:
    """Read current printing prices in INR."""
    return db.list_pricing()

@mcp.tool(annotations=read)
def get_auto_print_settings() -> dict:
    """Read auto-print rules. Auto-print requires staff-confirmed payment."""
    return db.get_auto_print_settings() or {}

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True))
def print_paid_job(job_id: int, printer_name: str, confirm: bool = False) -> dict:
    """Physically print one paid, queued job. Obtain the user's explicit confirmation
    of job ID and printer before setting confirm=true. Cannot confirm payments or reprint."""
    if os.environ.get('TPRINT_MCP_ALLOW_PRINT', '0') != '1':
        raise ValueError('MCP printing disabled. Shop owner must enable it locally.')
    if not confirm:
        raise ValueError('Explicit confirmation required.')
    job = print_service.submit(job_id, printer_name, actor='claude_mcp')
    return {'job': summary(job), 'result': 'submitted; physical completion is not verified'}

class BearerGuard:
    def __init__(self, app):
        self.app = app
    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            headers = dict(scope['headers'])
            provided = headers.get(b'authorization', b'').decode('latin1')
            if not secrets.compare_digest(provided, 'Bearer ' + TOKEN):
                await JSONResponse({'error': 'Unauthorized'}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)

app = BearerGuard(mcp.streamable_http_app())

if __name__ == '__main__':
    import uvicorn
    if not db.config.DB_PATH.exists():
        raise RuntimeError('Start the shop server once before starting MCP.')
    print_service.initialize()
    uvicorn.run(app, host='127.0.0.1', port=8765)
