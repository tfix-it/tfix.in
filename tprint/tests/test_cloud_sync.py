"""End-to-end: the real print.tfix.in PHP inbox (../print/api, run with
`php -S` on SQLite) + this server's cloud sync. Skipped when PHP isn't
installed (e.g. on the shop PC)."""
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import types
import urllib.request
from pathlib import Path
from unittest.mock import Mock

import fitz
import httpx
import pytest

sys.modules.setdefault('win32print', types.ModuleType('win32print'))
os.environ.setdefault('TPRINT_MCP_TOKEN', 'test-only-token-' * 4)

from app import cloud_sync, config, db, print_service, printing  # noqa: E402

SITE = Path(__file__).resolve().parents[2] / 'print'
TOKEN = 'shop-token-for-tests-0123456789abcdefghijkl'
pytestmark = pytest.mark.skipif(shutil.which('php') is None or not (SITE / 'api' / 'shop.php').exists(),
                                reason='needs php and the print/ site next to tprint/')


def _free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.fixture
def site(tmp_path):
    data = tmp_path / 'cloud'
    data.mkdir()
    cfg = tmp_path / 'config.php'
    cfg.write_text("<?php return " + json.dumps({
        'db_dsn': f'sqlite:{data}/db.sqlite', 'data_dir': str(data),
        'shop_token_sha256': hashlib.sha256(TOKEN.encode()).hexdigest(),
    }).replace('{', '[').replace('}', ']').replace('":', '" =>') + ";")
    port = _free_port()
    proc = subprocess.Popen(['php', '-S', f'127.0.0.1:{port}', '-t', str(SITE)],
                            env={**os.environ, 'TPRINT_CLOUD_CONFIG': str(cfg)},
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f'http://127.0.0.1:{port}/api'
    for _ in range(50):
        try:
            urllib.request.urlopen(base + '/status.php', timeout=1)
        except urllib.error.HTTPError:
            break
        except OSError:
            time.sleep(0.1)
    yield types.SimpleNamespace(base=base, data=data)
    proc.terminate()
    proc.wait(5)


@pytest.fixture(autouse=True)
def shop(tmp_path, monkeypatch):
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    monkeypatch.setattr(config, 'DB_PATH', tmp_path / 'shop.db')
    monkeypatch.setattr(config, 'UPLOAD_DIR', uploads)
    monkeypatch.setattr(config, 'INITIAL_ADMIN_PASSWORD', 'test-password')
    db.init_db(); print_service.initialize(); cloud_sync.initialize()
    db.upsert_printer('Test', 'Test')
    monkeypatch.setattr(printing, 'print_pdf', Mock())


def pdf(tmp_path, pages, name='doc.pdf'):
    d = fitz.open()
    for _ in range(pages):
        d.new_page()
    p = tmp_path / name
    d.save(p)
    return p


def place_order(site, files, phone='9876543210'):
    r = httpx.post(site.base + '/order.php', data={
        'name': 'Anu', 'phone': phone, 'notes': 'cover in colour',
        'options': json.dumps([o for _, o in files]),
    }, files=[('files[]', (p.name, p.read_bytes(), 'application/pdf')) for p, _ in files])
    assert r.status_code == 201, r.text
    return r.json()


def status(site, order):
    return httpx.get(site.base + '/status.php', params={'code': order['code'], 't': order['token']}).json()


def client(site, token=TOKEN):
    return cloud_sync.CloudClient(site.base + '/shop.php', token, timeout=10)


BW = {'color': False, 'doubleside': False, 'paper_size': 'A4', 'pages_per_sheet': 1, 'copies': 1, 'pages_range': ''}


def test_order_while_closed_then_import_pay_print(site, tmp_path):
    order = place_order(site, [(pdf(tmp_path, 3), BW),
                               (pdf(tmp_path, 4, 'b.pdf'), {**BW, 'color': True, 'copies': 2, 'pages_range': '1-2'})])
    assert status(site, order)['status'] == 'pending'

    created = cloud_sync.sync_once(client(site))          # shop PC comes online
    assert len(created) == 2
    a, b = created
    # Priced by the shop's own table: 1st job of the day gets the B&W minimum
    # (3 pages: 2+2+2 -> 6 - 2 + 10 = 14); colour 2 pages x 2 copies x 10 = 40.
    assert (a['pages'], a['cost'], a['paid'], a['status']) == (3, 14, 0, 'queued')
    assert (b['pages'], b['cost'], b['pages_range']) == (2, 40, '1-2')
    assert a['original_filename'].startswith(f"[{order['code']} · Anu] doc.pdf")
    assert a['phone'] == '9876543210'

    online = status(site, order)
    assert online['status'] == 'received' and online['confirmed_total'] == 54
    assert [f['cost'] for f in online['files']] == [14, 40]
    assert list((site.data / 'uploads').iterdir()) == []   # website copy deleted

    # Not paid yet -> can't print.
    with pytest.raises(print_service.PrintBlocked):
        print_service.submit(a['id'], 'Test')

    for j in (a, b):
        print_service.confirm_payment(j['id'], True)
    cloud_sync.push_statuses(client(site))
    assert status(site, order)['status'] == 'paid'

    print_service.submit(a['id'], 'Test')
    cloud_sync.push_statuses(client(site))
    assert status(site, order)['status'] == 'printing'
    assert printing.print_pdf.call_count == 1

    print_service.submit(b['id'], 'Test')
    for j in (a, b):
        db.update_job(j['id'], status='ready')
    cloud_sync.push_statuses(client(site))
    assert status(site, order)['status'] == 'ready'
    for j in (a, b):
        db.update_job(j['id'], status='collected')
    assert cloud_sync.push_statuses(client(site)) == 1
    assert status(site, order)['status'] == 'collected'
    assert cloud_sync.push_statuses(client(site)) == 0    # finished orders aren't re-sent


def test_sync_is_idempotent(site, tmp_path):
    place_order(site, [(pdf(tmp_path, 2), BW)])
    assert len(cloud_sync.sync_once(client(site))) == 1
    assert cloud_sync.sync_once(client(site)) == []
    assert len(db.list_jobs()) == 1


def test_ack_lost_is_retried_without_duplicates(site, tmp_path, monkeypatch):
    order = place_order(site, [(pdf(tmp_path, 2), BW)])
    c = client(site)
    real_ack = c.ack
    monkeypatch.setattr(c, 'ack', Mock(side_effect=cloud_sync.CloudError('network down')))
    cloud_sync.sync_once(c)
    assert status(site, order)['status'] == 'pending'     # website didn't hear back
    monkeypatch.setattr(c, 'ack', real_ack)
    assert cloud_sync.sync_once(c) == []                  # nothing re-imported
    assert len(db.list_jobs()) == 1
    assert status(site, order)['status'] == 'received'


def test_bad_page_range_reported_to_customer(site, tmp_path):
    order = place_order(site, [(pdf(tmp_path, 2), {**BW, 'pages_range': '5-9'})])
    assert cloud_sync.sync_once(client(site)) == []
    f = status(site, order)['files'][0]
    assert f['job_status'] == 'failed' and 'outside' in f['error']
    assert db.list_jobs() == []


def test_wrong_token_rejected(site, tmp_path):
    place_order(site, [(pdf(tmp_path, 1), BW)])
    with pytest.raises(cloud_sync.CloudError, match='401'):
        cloud_sync.sync_once(client(site, token='x' * 40))
    assert db.list_jobs() == []


def test_website_down_changes_nothing(tmp_path):
    c = cloud_sync.CloudClient('http://127.0.0.1:9/api/shop.php', TOKEN, timeout=2)
    with pytest.raises(cloud_sync.CloudError):
        cloud_sync.sync_once(c)
    assert db.list_jobs() == []


def test_order_validation(site, tmp_path):
    fake = tmp_path / 'fake.pdf'
    fake.write_bytes(b'hello')
    r = httpx.post(site.base + '/order.php', data={'name': 'A', 'phone': '9876543210', 'options': json.dumps([BW])},
                   files=[('files[]', ('fake.pdf', fake.read_bytes(), 'application/pdf'))])
    assert r.status_code == 400
    r = httpx.post(site.base + '/order.php', data={'name': 'A', 'phone': '123', 'options': json.dumps([BW])},
                   files=[('files[]', ('a.pdf', pdf(tmp_path, 1).read_bytes(), 'application/pdf'))])
    assert r.status_code == 400
    order = place_order(site, [(pdf(tmp_path, 1), BW)])
    r = httpx.get(site.base + '/status.php', params={'code': order['code'], 't': '0' * 32})
    assert r.status_code == 404
    body = status(site, order)
    assert 'phone' not in body


def test_settings_work_with_old_config_py(monkeypatch):
    # An older, customised config.py has no CLOUD_* lines: fall back to env vars.
    for name in ('CLOUD_URL', 'CLOUD_TOKEN', 'CLOUD_SYNC_INTERVAL_SEC'):
        monkeypatch.delattr(config, name, raising=False)
    monkeypatch.delenv('PRINTSHOP_CLOUD_URL', raising=False)
    assert not cloud_sync.is_configured()
    monkeypatch.setenv('PRINTSHOP_CLOUD_URL', 'https://print.tfix.in/api/shop.php')
    monkeypatch.setenv('PRINTSHOP_CLOUD_TOKEN', 'x' * 40)
    assert cloud_sync.is_configured() and cloud_sync.sync_interval() == 60
