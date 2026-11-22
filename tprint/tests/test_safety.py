import os, sys, types, asyncio
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
import pytest
sys.modules.setdefault('win32print', types.ModuleType('win32print'))
os.environ['TPRINT_MCP_TOKEN'] = 'test-only-token-' * 4
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

def test_mcp_auth_and_protocol():
    from starlette.testclient import TestClient
    from app.mcp_server import app, TOKEN
    with TestClient(app,base_url='http://localhost') as client:
        assert client.post('/mcp',json={}).status_code==401
        headers={'Authorization':'Bearer '+TOKEN,'Accept':'application/json, text/event-stream'}
        r=client.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'test','version':'1'}}})
        assert r.status_code==200, r.text
        r=client.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':2,'method':'tools/list'})
        names={t['name'] for t in r.json()['result']['tools']}
        assert names=={'list_print_jobs','list_printers','get_pricing','get_auto_print_settings','print_paid_job'}
        r=client.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'get_pricing','arguments':{}}})
        assert not r.json()['result'].get('isError')
        r=client.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'print_paid_job','arguments':{'job_id':job(),'printer_name':'Test','confirm':True}}})
        assert r.json()['result']['isError'];printing.print_pdf.assert_not_called()

def test_manual_endpoint_rejects_unpaid():
    from app.routes_admin import admin_print_job
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc: asyncio.run(admin_print_job(job(0),'Test'))
    assert exc.value.status_code==409
    printing.print_pdf.assert_not_called()

def test_mcp_print_confirmation_and_duplicate(monkeypatch):
    from app.mcp_server import print_paid_job
    monkeypatch.setenv('TPRINT_MCP_ALLOW_PRINT','1')
    i=job()
    with pytest.raises(ValueError): print_paid_job(i,'Test',False)
    assert print_paid_job(i,'Test',True)['job']['status']=='printing'
    with pytest.raises(service.PrintBlocked): print_paid_job(i,'Test',True)
    assert printing.print_pdf.call_count==1

def test_migration_repeat_preserves_data():
    i=job();db.init_db();service.initialize();service.initialize()
    assert db.get_job(i)['cost']==2
