import os, sys, types, asyncio
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
import pytest
sys.modules.setdefault('win32print', types.ModuleType('win32print'))
from app import config, db, print_service as service, printing

@pytest.fixture(autouse=True)
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DB_PATH', tmp_path / 'test.db')
    monkeypatch.setattr(config, 'UPLOAD_DIR', tmp_path)
    monkeypatch.setattr(config, 'INITIAL_ADMIN_PASSWORD', 'test-password')
    db.init_db(); service.initialize()
    db.upsert_printer('Test', 'Test')
    (tmp_path / 'file.pdf').write_bytes(b'%PDF-test')
    monkeypatch.setattr(printing, 'print_pdf', Mock())

def job(paid=1):
    return db.create_job(phone='000',original_filename='test.pdf',pdf_filename='file.pdf',file_type='pdf',
        pages=1,sheets=1,copies=1,color=0,doubleside=0,pages_per_sheet=1,paper_size='A4',cost=2,paid=paid)

def test_unpaid_blocked():
    with pytest.raises(service.PrintBlocked): service.submit(job(0), 'Test')
    printing.print_pdf.assert_not_called()

def test_concurrent_claim_survives_status_reset():
    i=job()
    def attempt(_):
        try: service.submit(i,'Test'); return True
        except service.PrintBlocked: return False
    with ThreadPoolExecutor(4) as pool: assert sum(pool.map(attempt,range(4))) == 1
    db.update_job(i,status='queued')
    with pytest.raises(service.PrintBlocked): service.submit(i,'Test')
    assert printing.print_pdf.call_count == 1

def test_uncertain_failure_not_retried():
    i=job();printing.print_pdf.side_effect=TimeoutError()
    with pytest.raises(service.PrintBlocked): service.submit(i,'Test')
    db.update_job(i,status='queued')
    with pytest.raises(service.PrintBlocked): service.submit(i,'Test')
    assert printing.print_pdf.call_count == 1

def test_printer_file_validation():
    i=job(); db.set_printer_enabled(db.list_printers()[0]['id'],False)
    with pytest.raises(service.PrintBlocked): service.submit(i,'Test')
    db.set_printer_enabled(db.list_printers()[0]['id'],True)
    db.update_job(i,pdf_filename='../outside.pdf')
    with pytest.raises(service.PrintBlocked): service.submit(i,'Test')
    printing.print_pdf.assert_not_called()

def test_payment_audit_and_revoke():
    i=job(0);service.confirm_payment(i,True);service.submit(i,'Test')
    with pytest.raises(service.PrintBlocked): service.confirm_payment(i,False)
    with db.get_conn() as c: assert c.execute('SELECT COUNT(*) FROM payment_audit').fetchone()[0]==1

def test_auto_print_only_after_staff_payment():
    from app.routes_customer import maybe_auto_print
    i=job(0);db.update_auto_print_settings(True,'Test',True,10,10)
    maybe_auto_print(db.get_job(i));printing.print_pdf.assert_not_called()
    from app.routes_admin import admin_mark_paid
    asyncio.run(admin_mark_paid(i,True));assert printing.print_pdf.call_count==1
    asyncio.run(admin_mark_paid(i,True));assert printing.print_pdf.call_count==1

TOKEN = 'test-only-token-' * 4
INIT = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
    'protocolVersion': '2025-11-25', 'clientInfo': {'name': 'test', 'version': '1'},
    'capabilities': {'extensions': {'io.modelcontextprotocol/ui': {'mimeTypes': ['text/html;profile=mcp-app']}}}}}


def mcp_client():
    from starlette.testclient import TestClient
    from app.mcp_server import create_http_app
    return TestClient(create_http_app(TOKEN), base_url='http://localhost')


def rpc(client, rid, method, params=None):
    headers = {'Authorization': 'Bearer ' + TOKEN, 'Accept': 'application/json, text/event-stream',
               'MCP-Protocol-Version': '2025-11-25'}
    body = {'jsonrpc': '2.0', 'id': rid, 'method': method}
    if params is not None:
        body['params'] = params
    r = client.post('/mcp', headers=headers, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_mcp_requires_token():
    from app.mcp_server import create_http_app
    with pytest.raises(RuntimeError):
        create_http_app('short')
    with mcp_client() as client:
        assert client.post('/mcp', json=INIT).status_code == 401
        assert client.post('/mcp', json=INIT, headers={'Authorization': 'Bearer wrong'}).status_code == 401


def test_mcp_auth_and_protocol():
    with mcp_client() as client:
        init = client.post('/mcp', json=INIT, headers={'Authorization': 'Bearer ' + TOKEN,
                                                         'Accept': 'application/json, text/event-stream'})
        assert init.status_code == 200, init.text
        tools = {t['name']: t for t in rpc(client, 2, 'tools/list')['result']['tools']}
        assert set(tools) == {'open_print_dashboard', 'list_print_jobs', 'list_printers', 'get_pricing',
                              'get_auto_print_settings', 'print_paid_job'}
        assert tools['open_print_dashboard']['_meta']['ui']['resourceUri'] == 'ui://tprint/dashboard.html'
        r = rpc(client, 3, 'tools/call', {'name': 'get_pricing', 'arguments': {}})
        assert not r['result'].get('isError')
        r = rpc(client, 4, 'tools/call', {'name': 'print_paid_job',
                                          'arguments': {'job_id': job(), 'printer_name': 'Test', 'confirm': True}})
        assert r['result']['isError'] and 'printing disabled' in r['result']['content'][0]['text']
        printing.print_pdf.assert_not_called()


def test_mcp_app_resource_and_dashboard():
    db.create_job(phone='9876543210', original_filename='secret-name.pdf', pdf_filename='file.pdf',
                  file_type='pdf', pages=3, sheets=3, copies=2, color=1, doubleside=0, pages_per_sheet=1,
                  paper_size='A4', cost=60, paid=0)
    job()
    with mcp_client() as client:
        res = rpc(client, 1, 'resources/list')['result']['resources']
        assert any(r['uri'] == 'ui://tprint/dashboard.html' and r['mimeType'] == 'text/html;profile=mcp-app' for r in res)
        content = rpc(client, 2, 'resources/read', {'uri': 'ui://tprint/dashboard.html'})['result']['contents'][0]
        assert content['mimeType'] == 'text/html;profile=mcp-app'
        assert 'ui/initialize' in content['text'] and '<script' in content['text']
        r = rpc(client, 3, 'tools/call', {'name': 'open_print_dashboard', 'arguments': {'status': 'queued'}})['result']
        assert not r.get('isError')
        snap = r['structuredContent']
        assert snap['counts']['queued'] == 2 and snap['counts']['awaiting_payment'] == 1
        assert len(snap['jobs']) == 2 and snap['printing_enabled'] is False
        assert snap['printers'][0]['windows_printer_name'] == 'Test'
        text = r['content'][0]['text']
        assert 'awaiting payment' in text
        # Customer identity never leaves the shop PC, in either the UI data or the model text.
        for leaked in ('9876543210', 'secret-name'):
            assert leaked not in str(snap) and leaked not in text
        bad = rpc(client, 4, 'tools/call', {'name': 'open_print_dashboard', 'arguments': {'status': 'nope'}})['result']
        assert bad['isError'] and 'status must be one of' in bad['content'][0]['text']


def test_manual_endpoint_rejects_unpaid():
    from app.routes_admin import admin_print_job
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc: asyncio.run(admin_print_job(job(0),'Test'))
    assert exc.value.status_code==409
    printing.print_pdf.assert_not_called()

def test_mcp_print_confirmation_and_duplicate(monkeypatch):
    from app.mcp_server import print_paid_job, ToolError
    monkeypatch.setenv('TPRINT_MCP_ALLOW_PRINT','1')
    i=job()
    with pytest.raises(ToolError): print_paid_job(i,'Test',False)
    assert print_paid_job(i,'Test',True)['job']['status']=='printing'
    with pytest.raises(ToolError, match='Only queued|Already dispatched'): print_paid_job(i,'Test',True)
    assert printing.print_pdf.call_count==1

def test_migration_repeat_preserves_data():
    i=job();db.init_db();service.initialize();service.initialize()
    assert db.get_job(i)['cost']==2
