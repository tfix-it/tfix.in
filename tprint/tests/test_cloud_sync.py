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


def place_order(site, files, phone='9876543210', expect=201):
    r = httpx.post(site.base + '/order.php', data={
        'name': 'Anu', 'phone': phone, 'notes': 'cover in colour',
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
    done = status(site, order)
    assert done['status'] == 'collected'
    assert cloud_sync.push_statuses(client(site)) == 0    # finished orders aren't re-sent
    # CLUTCH Rewards: ₹54 of prints so far -> no ₹5 yet, ₹46 to go
    assert done['rewards'] == {'phone': '98******10', 'spent': 54, 'earned': 0, 'redeemed': 0, 'balance': 0,
                               'step': 100, 'per_step': 5, 'next_in': 46, 'order_reward': 0}


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


# ---------------- CLUTCH Rewards: mobile number = rewards ID, ₹5 per ₹100 ----------------
def collect(site, order, costs, pay=True):
    """Shop PC 1 imports the order, staff set the final prices, mark paid and collected."""
    jobs = cloud_sync.sync_once(client(site))
    assert len(jobs) == len(costs)
    for j, cost in zip(jobs, costs):
        db.update_job(j['id'], cost=cost)                 # staff price correction must count
        if pay:
            print_service.confirm_payment(j['id'], True)
        db.update_job(j['id'], status='collected' if pay else 'ready')
    cloud_sync.push_statuses(client(site))
    return status(site, order)


def test_no_pin_or_sign_in(site, tmp_path):
    assert not (SITE / 'api' / 'account.php').exists()
    a = place_order(site, [(pdf(tmp_path, 1), BW)])
    b = place_order(site, [(pdf(tmp_path, 1), BW)])        # same number again: no PIN asked
    assert set(a) == {'code', 'token', 'status'} and a['code'] != b['code']
    place_order(site, [(pdf(tmp_path, 1), BW)], phone='', expect=400)   # mobile number is mandatory


def test_rewards_5_for_every_100(site, tmp_path):
    o1 = place_order(site, [(pdf(tmp_path, 1), BW)])
    r = collect(site, o1, [60])['rewards']
    assert (r['spent'], r['earned'], r['balance'], r['next_in'], r['order_reward']) == (60, 0, 0, 40, 0)

    o2 = place_order(site, [(pdf(tmp_path, 1), BW)], phone='+91 98765 43210')   # same customer
    r = collect(site, o2, [40])['rewards']
    assert (r['spent'], r['earned'], r['balance'], r['next_in'], r['order_reward']) == (100, 5, 5, 100, 5)

    o3 = place_order(site, [(pdf(tmp_path, 1), BW), (pdf(tmp_path, 2, 'b.pdf'), BW)])
    r = collect(site, o3, [150, 100])['rewards']
    assert (r['spent'], r['earned'], r['balance'], r['order_reward']) == (350, 15, 15, 10)
    assert status(site, o3)['confirmed_total'] == 250

    # credited once: re-pushing (or the website hearing "collected" again) adds nothing
    with db.get_conn() as conn:
        conn.execute('UPDATE cloud_orders SET done=0, last_pushed=NULL')
    cloud_sync.push_statuses(client(site))
    assert status(site, o1)['rewards']['earned'] == 15

    # another number has its own rewards; an order that isn't collected earns nothing yet
    other = place_order(site, [(pdf(tmp_path, 1), BW)], phone='9123456789')
    s = collect(site, other, [500], pay=False)
    assert s['status'] == 'ready' and s['rewards']['earned'] == 0 and s['rewards']['phone'] == '91******89'


def test_use_rewards_at_the_counter(site, tmp_path, monkeypatch):
    collect(site, place_order(site, [(pdf(tmp_path, 1), BW)]), [210])
    c = client(site)
    info = c.rewards('98765 43210')
    assert info['phone'] == '9876543210' and info['name'] == 'Anu' and info['balance'] == 10
    assert info['credits'][0]['code'].startswith('CP-') and info['credits'][0]['reward'] == 10
    with pytest.raises(cloud_sync.CloudError, match='409'):
        c.redeem('9876543210', 15)                         # more than available
    with pytest.raises(cloud_sync.CloudError, match='400'):
        c.redeem('9876543210', -5)
    with pytest.raises(cloud_sync.CloudError, match='401'):
        client(site, token='x' * 40).redeem('9876543210', 5)
    assert c.redeem('9876543210', 5, 'CP-102')['balance'] == 5
    info = c.rewards('9876543210')
    assert (info['earned'], info['redeemed'], info['balance']) == (10, 5, 5)
    assert info['redemptions'][0] == {'amount': 5, 'note': 'CP-102', 'at': info['redemptions'][0]['at']}
    assert c.rewards('9000000000')['balance'] == 0          # never ordered

    # the T Print admin routes do the same through cloud_sync
    import asyncio
    from fastapi import HTTPException
    from app.routes_admin import admin_customer_rewards, admin_redeem_rewards
    monkeypatch.setenv('PRINTSHOP_CLOUD_URL', site.base + '/shop.php')
    monkeypatch.setenv('PRINTSHOP_CLOUD_TOKEN', TOKEN)
    for name in ('CLOUD_URL', 'CLOUD_TOKEN'):
        monkeypatch.delattr(config, name, raising=False)
    assert asyncio.run(admin_customer_rewards('9876543210'))['balance'] == 5
    assert asyncio.run(admin_redeem_rewards('9876543210', 5, ''))['balance'] == 0
    with pytest.raises(HTTPException) as e:
        asyncio.run(admin_redeem_rewards('9876543210', 5, ''))
    assert e.value.status_code == 409


def test_online_job_label_is_html_escaped_in_admin():
    admin = (Path(__file__).resolve().parents[1] / 'static' / 'admin' / 'index.html').read_text()
    assert '${esc(job.original_filename)}' in admin and '${esc(truncate(job.original_filename, 16))}' in admin
