"""Customer API hardening: server-side page counting, upload-name checks,
input bounds, and no phone numbers / filenames in public job lookups."""
import sys, types, os
from unittest.mock import Mock

import fitz
import pytest

sys.modules.setdefault('win32print', types.ModuleType('win32print'))
os.environ.setdefault('TPRINT_MCP_TOKEN', 'test-only-token-' * 4)

from fastapi import FastAPI
from starlette.testclient import TestClient

from app import config, db, print_service, printing, routes_customer, security


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    monkeypatch.setattr(config, 'DB_PATH', tmp_path / 'test.db')
    monkeypatch.setattr(config, 'UPLOAD_DIR', uploads)
    monkeypatch.setattr(config, 'INITIAL_ADMIN_PASSWORD', 'test-password')
    db.init_db(); print_service.initialize()
    security._rate_buckets.clear()
    monkeypatch.setattr(printing, 'print_pdf', Mock())
    # A 3-page PDF inside uploads, and a secret PDF outside it.
    for path, n in ((uploads / 'doc.pdf', 3), (tmp_path / 'secret.pdf', 1)):
        d = fitz.open()
        for _ in range(n):
            d.new_page()
        d.save(path)
    return tmp_path


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(routes_customer.router)
    with TestClient(app) as c:
        yield c


def submit(client, **over):
    form = dict(phone='9000000001', pdf_filename='doc.pdf', original_filename='notes.pdf',
                file_type='pdf', pages='1', color='false', doubleside='false',
                pages_per_sheet='1', copies='1', paper_size='A4', pages_range='')
    form.update({k: str(v) for k, v in over.items()})
    return client.post('/api/jobs', data=form)


def test_page_count_comes_from_pdf_not_browser(client):
    # Browser claims 1 page; the PDF has 3. Second job of the day so the
    # minimum charge is waived and the plain A4 B&W rate (₹2/page) applies.
    submit(client)
    r = submit(client, pages=1)
    assert r.status_code == 200, r.text
    job = db.get_job(r.json()['id'])
    assert job['pages'] == 3 and job['sheets'] == 3 and job['cost'] == 6


def test_page_range_is_applied_server_side(client):
    submit(client)
    r = submit(client, pages=99, pages_range='2-3')
    assert r.status_code == 200, r.text
    job = db.get_job(r.json()['id'])
    assert job['pages'] == 2 and job['pages_range'] == '2-3' and job['cost'] == 4


@pytest.mark.parametrize('rng', ['0-2', '2-9', '3-1', 'x', ',,'])
def test_bad_page_range_rejected(client, rng):
    assert submit(client, pages_range=rng).status_code == 400


@pytest.mark.parametrize('name', ['../secret.pdf', 'sub/doc.pdf', '/etc/passwd', '', 'missing.pdf'])
def test_upload_name_must_be_a_file_in_uploads(client, setup, name):
    if name == '/etc/passwd':
        name = str(setup / 'secret.pdf')  # absolute path to a real PDF
    assert submit(client, pdf_filename=name).status_code in (400, 422)


@pytest.mark.parametrize('field,value', [('copies', 0), ('copies', -3), ('copies', 501),
                                         ('pages_per_sheet', 0), ('pages_per_sheet', 3)])
def test_bounds(client, field, value):
    assert submit(client, **{field: value}).status_code == 400


def test_public_lookups_hide_phone_and_files(client):
    created = submit(client).json()
    assert created['phone'] == '9000000001'  # the submitter gets their own job back
    for body in (client.get(f"/api/jobs/{created['id']}").json(),
                 client.get('/api/jobs/by-phone/9000000001').json()[0]):
        assert body['status'] == 'queued' and body['cost'] == created['cost']
        for hidden in ('phone', 'pdf_filename', 'source_filename', 'original_filename'):
            assert hidden not in body


def test_customer_websocket_gets_public_view(client):
    import asyncio
    from app.ws_hub import hub
    created = submit(client).json()
    with client.websocket_connect(f"/ws/customer/{created['id']}") as ws:
        asyncio.run(hub.broadcast_job(db.get_job(created['id'])))
        msg = ws.receive_json()
    assert msg['job']['id'] == created['id']
    assert 'phone' not in msg['job'] and 'pdf_filename' not in msg['job']
