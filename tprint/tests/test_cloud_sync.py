"""End-to-end: the real print.tfix.in PHP inbox (../print/api, run with
`php -S` on SQLite) + this server's cloud sync. Skipped when PHP isn't
installed (e.g. on the shop PC)."""
import hashlib
import json
import os
import re
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


def place_order(site, files, phone='9876543210', pin='1234', expect=201):
    r = httpx.post(site.base + '/order.php', data={
        'name': 'Anu', 'phone': phone, 'pin': pin, 'notes': 'cover in colour',
        'options': json.dumps([o for _, o in files]),
    }, files=[('files[]', (p.name, p.read_bytes(), 'application/pdf')) for p, _ in files])
    assert r.status_code == expect, r.text
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
    assert re.fullmatch(r'CP-\d{3,}', order['code'])      # pickup token, e.g. CP-101

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
    r = httpx.post(site.base + '/order.php', data={'name': 'A', 'phone': '9876543210', 'pin': '1234', 'options': json.dumps([BW])},
                   files=[('files[]', ('fake.pdf', fake.read_bytes(), 'application/pdf'))])
    assert r.status_code == 400
    r = httpx.post(site.base + '/order.php', data={'name': 'A', 'phone': '123', 'pin': '1234', 'options': json.dumps([BW])},
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


def test_server_status_heartbeat(site, tmp_path):
    def server():
        return httpx.get(site.base + '/server.php').json()
    assert server()['online'] is False and server()['last_seen'] is None   # shop PC never connected
    with pytest.raises(cloud_sync.CloudError):
        cloud_sync.sync_once(client(site, token='x' * 40))                 # wrong key: no heartbeat
    assert server()['online'] is False
    cloud_sync.sync_once(client(site))                                     # shop PC 1 syncs
    s = server()
    assert s['online'] is True and s['seconds_ago'] <= 5
    (site.data / 'heartbeat').write_text(str(int(time.time()) - 600))       # 10 minutes silent
    assert server()['online'] is False


# ---------------- CLUTCHPRINT accounts: mobile number = username, 4-digit PIN ----------------
def login(site, phone, pin):
    return httpx.post(site.base + '/account.php?action=login', data={'phone': phone, 'pin': pin})


def my_orders(site, session):
    return httpx.get(site.base + '/account.php?action=orders', headers={'X-Client-Session': session or ''})


def test_first_order_creates_account_later_orders_need_the_pin(site, tmp_path):
    first = place_order(site, [(pdf(tmp_path, 1), BW)], pin='4321')
    assert first['new_account'] is True and len(first['session']) == 48
    place_order(site, [(pdf(tmp_path, 1), BW)], pin='0000', expect=401)     # someone else, wrong PIN
    place_order(site, [(pdf(tmp_path, 1), BW)], pin='12a4', expect=400)     # not 4 digits
    again = place_order(site, [(pdf(tmp_path, 1), BW)], pin='4321')
    assert again['new_account'] is False
    assert again['code'] != first['code']


def test_sign_in_lists_only_my_tokens(site, tmp_path):
    a = place_order(site, [(pdf(tmp_path, 1), BW)], phone='9876543210', pin='1111')
    b = place_order(site, [(pdf(tmp_path, 2), BW)], phone='9876543210', pin='1111')
    place_order(site, [(pdf(tmp_path, 1), BW)], phone='9123456789', pin='2222')   # another customer
    assert login(site, '9000000000', '1111').status_code == 404                  # never ordered
    assert login(site, '9876543210', '9999').status_code == 401
    r = login(site, '+91 98765 43210', '1111')
    assert r.status_code == 200 and r.json()['phone'] == '98******10'
    body = my_orders(site, r.json()['session']).json()
    assert [o['code'] for o in body['orders']] == [b['code'], a['code']]        # newest first
    assert all('phone' not in o for o in body['orders'])
    assert my_orders(site, None).status_code == 401
    assert my_orders(site, 'f' * 48).status_code == 401
    httpx.post(site.base + '/account.php?action=logout', headers={'X-Client-Session': r.json()['session']})
    assert my_orders(site, r.json()['session']).status_code == 401              # signed out


def test_wrong_pins_lock_the_number(site, tmp_path):
    place_order(site, [(pdf(tmp_path, 1), BW)], pin='1111')
    for _ in range(5):
        assert login(site, '9876543210', '0000').status_code == 401
    assert login(site, '9876543210', '1111').status_code == 429                 # locked, even with the right PIN


def test_staff_reset_pin_via_t_print(site, tmp_path, monkeypatch):
    first = place_order(site, [(pdf(tmp_path, 1), BW)], pin='1111')
    res = client(site).reset_pin('9876543210')
    new_pin = res['pin']
    assert len(new_pin) == 4 and new_pin.isdigit()
    if new_pin != '1111':
        assert login(site, '9876543210', '1111').status_code == 401
    assert my_orders(site, first['session']).status_code == 401                 # old sessions signed out
    assert login(site, '9876543210', new_pin).status_code == 200
    with pytest.raises(cloud_sync.CloudError, match='404'):
        client(site).reset_pin('9123456789')                                    # no account
    with pytest.raises(cloud_sync.CloudError, match='401'):
        client(site, token='x' * 40).reset_pin('9876543210')                    # wrong shop key

    # the T Print admin route does the same through cloud_sync
    import asyncio
    from app.routes_admin import admin_reset_customer_pin
    monkeypatch.setenv('PRINTSHOP_CLOUD_URL', site.base + '/shop.php')
    monkeypatch.setenv('PRINTSHOP_CLOUD_TOKEN', TOKEN)
    for name in ('CLOUD_URL', 'CLOUD_TOKEN'):
        monkeypatch.delattr(config, name, raising=False)
    out = asyncio.run(admin_reset_customer_pin('98765 43210'))
    assert out['phone'] == '9876543210' and login(site, '9876543210', out['pin']).status_code == 200


def test_change_pin(site, tmp_path):
    s = place_order(site, [(pdf(tmp_path, 1), BW)], pin='1111')['session']
    h = {'X-Client-Session': s}
    assert httpx.post(site.base + '/account.php?action=change_pin', headers=h, data={'pin': '0000', 'new_pin': '2468'}).status_code == 401
    assert httpx.post(site.base + '/account.php?action=change_pin', headers=h, data={'pin': '1111', 'new_pin': '2468'}).status_code == 200
    assert login(site, '9876543210', '2468').status_code == 200


def test_online_job_label_is_html_escaped_in_admin():
    admin = (Path(__file__).resolve().parents[1] / 'static' / 'admin' / 'index.html').read_text()
    assert '${esc(job.original_filename)}' in admin and '${esc(truncate(job.original_filename, 16))}' in admin


def test_signed_in_customer_orders_without_pin(site, tmp_path):
    s = place_order(site, [(pdf(tmp_path, 1), BW)], pin='1111')['session']
    r = httpx.post(site.base + '/order.php', headers={'X-Client-Session': s},
                   data={'name': 'Anu', 'phone': '9876543210', 'options': json.dumps([BW])},
                   files=[('files[]', ('a.pdf', pdf(tmp_path, 1).read_bytes(), 'application/pdf'))])
    assert r.status_code == 201, r.text
    # the session only covers its own mobile number
    r = httpx.post(site.base + '/order.php', headers={'X-Client-Session': s},
                   data={'name': 'X', 'phone': '9123456789', 'options': json.dumps([BW])},
                   files=[('files[]', ('a.pdf', pdf(tmp_path, 1).read_bytes(), 'application/pdf'))])
    assert r.status_code == 400
