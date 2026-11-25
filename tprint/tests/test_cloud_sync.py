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
    push_port = _free_port()   # stand-in push service for the ready-alert tests
    cfg.write_text("<?php return " + json.dumps({
        'db_dsn': f'sqlite:{data}/db.sqlite', 'data_dir': str(data),
        'shop_token_sha256': hashlib.sha256(TOKEN.encode()).hexdigest(),
        'push_test_hosts': [f'127.0.0.1:{push_port}'],
    }).replace('{', '[').replace('}', ']').replace('":', '" =>') + ";")
    port = _free_port()
    proc = subprocess.Popen(['php', '-S', f'127.0.0.1:{port}', '-t', str(SITE)],
                            env={**os.environ, 'TPRINT_CLOUD_CONFIG': str(cfg)},
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f'http://127.0.0.1:{port}/api'
    rport = _free_port()   # repair.tfix.in (shares the same database and rewards)
    rproc = subprocess.Popen(['php', '-S', f'127.0.0.1:{rport}', '-t', str(SITE.parent / 'repair')],
                             env={**os.environ, 'TPRINT_CLOUD_CONFIG': str(cfg)},
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rbase = f'http://127.0.0.1:{rport}/api'
    for b in (base, rbase):
        for _ in range(50):
            try:
                urllib.request.urlopen(b + '/status.php', timeout=1)
            except urllib.error.HTTPError:
                break
            except OSError:
                time.sleep(0.1)
    yield types.SimpleNamespace(base=base, rbase=rbase, data=data, push_port=push_port)
    for pr in (proc, rproc):
        pr.terminate()
        pr.wait(5)


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
    hist = done['rewards'].pop('history')
    assert [(h['kind'], h['amount'], h['points']) for h in hist] == [('credit', 54, 0)]   # spend is logged too
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


# ---------------- tamper-evident rewards log ----------------
def test_rewards_log_is_hash_chained_and_tamper_evident(site, tmp_path):
    import sqlite3
    o1 = place_order(site, [(pdf(tmp_path, 1), BW)])
    collect(site, o1, [120])
    c = client(site)
    c.redeem('9876543210', 5, 'used at counter')
    collect(site, place_order(site, [(pdf(tmp_path, 1), BW)]), [90])

    info = c.rewards('9876543210')
    hist = info['history']
    assert [(h['kind'], h['points'], h['balance']) for h in hist] == [('credit', 5, 5), ('redeem', -5, 0), ('credit', 5, 5)]
    assert hist[0]['code'].startswith('CP-') and len(hist[0]['receipt']) == 12
    assert info['ledger'] == {'ok': True, 'entries': 3, 'head': info['ledger']['head'], 'problem': None}

    # the customer's pass shows the same history (without staff notes)
    mine = status(site, o1)['rewards']['history']
    assert [h['receipt'] for h in mine] == [h['receipt'] for h in hist] and all('note' not in h for h in mine)

    # someone edits an old entry directly in the database -> detected
    db_path = site.data / 'db.sqlite'
    with sqlite3.connect(db_path) as con:
        con.execute("UPDATE reward_ledger SET points = 50, balance_after = 50 WHERE seq = 1")
    bad = c._json('GET', 'ledger_verify')
    assert bad['ok'] is False and 'entry 1' in bad['problem']
    with sqlite3.connect(db_path) as con:
        con.execute("UPDATE reward_ledger SET points = 5, balance_after = 5 WHERE seq = 1")
    assert c._json('GET', 'ledger_verify')['ok'] is True
    # extra rewards added outside the log -> totals mismatch
    with sqlite3.connect(db_path) as con:
        con.execute("INSERT INTO reward_credits (order_id, phone, amount, reward, created_at) VALUES (999, '9876543210', 1000, 50, '2026-01-01 00:00:00')")
    assert 'totals' in c._json('GET', 'ledger_verify')['problem']
    # deleting an entry -> detected
    with sqlite3.connect(db_path) as con:
        con.execute("DELETE FROM reward_credits WHERE order_id = 999")
        con.execute("DELETE FROM reward_ledger WHERE seq = 2")
    assert c._json('GET', 'ledger_verify')['ok'] is False


def test_rewards_given_before_the_log_are_backfilled(site, tmp_path):
    import sqlite3
    collect(site, place_order(site, [(pdf(tmp_path, 1), BW)]), [200])
    db_path = site.data / 'db.sqlite'
    with sqlite3.connect(db_path) as con:                      # pretend the log is new
        con.execute('DELETE FROM reward_ledger')
    (site.data / '.schema-v6').unlink()
    info = client(site).rewards('9876543210')
    assert info['ledger']['ok'] and info['ledger']['entries'] == 1
    assert info['history'][0]['points'] == 10 and info['history'][0]['balance'] == 10


def test_staff_cancel_open_orders(site, tmp_path):
    a = place_order(site, [(pdf(tmp_path, 1), BW)])
    b = place_order(site, [(pdf(tmp_path, 2), BW)])
    done = place_order(site, [(pdf(tmp_path, 1), BW)])
    cloud_sync.sync_once(client(site))
    with db.get_conn() as conn:                                # finish the 3rd order
        jobs = [dict(r) for r in conn.execute('SELECT * FROM jobs').fetchall()]
    job3 = [j for j in jobs if done['code'] in j['original_filename']][0]
    print_service.confirm_payment(job3['id'], True); db.update_job(job3['id'], status='collected')
    cloud_sync.push_statuses(client(site))
    c = place_order(site, [(pdf(tmp_path, 1), BW)])            # still waiting online, file on the server
    out = client(site)._json('POST', 'cancel', body={'all_open': True})
    assert sorted(out['cancelled']) == sorted([a['code'], b['code'], c['code']])
    assert [status(site, o)['status'] for o in (a, b, c, done)] == ['cancelled'] * 3 + ['collected']
    assert list((site.data / 'uploads').iterdir()) == []          # waiting file deleted
    assert client(site).pending() == []
    assert client(site)._json('POST', 'cancel', body={'all_open': True})['cancelled'] == []
    with pytest.raises(cloud_sync.CloudError, match='401'):
        client(site, token='x' * 40)._json('POST', 'cancel', body={'all_open': True})


def test_cancelled_order_stays_cancelled_when_shop_pushes(site, tmp_path):
    o = place_order(site, [(pdf(tmp_path, 1), BW)])
    job = cloud_sync.sync_once(client(site))[0]
    client(site)._json('POST', 'cancel', body={'all_open': True})
    print_service.confirm_payment(job['id'], True)
    cloud_sync.push_statuses(client(site))
    assert status(site, o)['status'] == 'cancelled'



# ---------------- repair.tfix.in: booking, staff updates, shared rewards ----------------
def book_repair(site, expect=201, **over):
    body = {'name': 'Anu', 'phone': '9876543210', 'device_type': 'phone', 'brand': 'Samsung', 'model': 'A54',
            'issues': ['screen', 'charging'], 'description': 'lines on screen',
            'diagnosis': {'tests': {'touch': {'result': 'fail', 'detail': '3/48 zones not touched'},
                                    'speaker': {'result': 'pass', 'detail': 'tone heard'},
                                    'bogus key!': {'result': 'pass'}, 'mic': {'result': 'weird'}},
                          'device': {'model': 'SM-A546E', 'screen': '1080x2340'}}}
    body.update(over)
    r = httpx.post(site.rbase + '/book.php', json=body)
    assert r.status_code == expect, r.text
    return r.json()


def rstatus(site, b):
    return httpx.get(site.rbase + '/status.php', params={'code': b['code'], 't': b['token']}).json()


def test_repair_booking_and_status(site):
    b = book_repair(site)
    assert re.fullmatch(r'RP-\d{3,}', b['code'])
    s = rstatus(site, b)
    assert s['status'] == 'requested' and s['issues'] == ['screen', 'charging'] and s['has_diagnosis']
    assert 'phone' not in s and s['rewards']['phone'] == '98******10'
    # private link needed
    assert httpx.get(site.rbase + '/status.php', params={'code': b['code'], 't': '0' * 32}).status_code == 404
    # validation
    book_repair(site, expect=400, phone='123')
    book_repair(site, expect=400, name='')
    book_repair(site, expect=400, device_type='car')
    book_repair(site, expect=400, issues=[], description='')
    book_repair(site, expect=400, visit_date='2001-01-01')
    # lib.php is never served from the repair site either
    assert httpx.get(site.rbase.replace('/api', '') + '/../print/api/lib.php').status_code in (403, 404)


def test_staff_sees_report_and_collect_earns_shared_rewards(site, tmp_path):
    c = client(site)
    b = book_repair(site)
    [r] = c.repairs()
    assert r['code'] == b['code'] and r['phone'] == '9876543210'
    assert set(r['diagnosis']['tests']) == {'touch', 'speaker'}          # bad keys/values dropped
    assert r['diagnosis']['tests']['touch']['result'] == 'fail'
    rid = r['repair_id']
    c.repair_update(rid, status='received', quote=1500, note='Screen + charging port')
    with pytest.raises(cloud_sync.CloudError, match='400'):
        c.repair_update(rid, status='collected')                         # needs final amount + paid
    c.repair_update(rid, status='ready', final_amount=1450)
    s = rstatus(site, b)
    assert (s['status'], s['quote'], s['final_amount'], s['note']) == ('ready', 1500, 1450, 'Screen + charging port')
    out = c.repair_update(rid, status='collected', paid=True)
    assert out['reward'] == 70                                           # ₹1450 -> 14 x ₹5
    with pytest.raises(cloud_sync.CloudError, match='409'):
        c.repair_update(rid, status='ready')
    c.repair_update(rid, status='collected', paid=True)                  # again: no double credit
    assert c.repairs() == [] and len(c.repairs(include_finished=True)) == 1

    # print + repair share one rewards balance and one verified log
    collect(site, place_order(site, [(pdf(tmp_path, 1), BW)]), [60])     # 1450 + 60 = 1510 -> 75
    info = c.rewards('9876543210')
    assert (info['spent'], info['earned'], info['balance']) == (1510, 75, 75)
    assert [h['code'][:3] for h in info['history']] == ['CP-', 'RP-'] and info['ledger']['ok']
    assert rstatus(site, b)['rewards']['balance'] == 75
    with pytest.raises(cloud_sync.CloudError, match='401'):
        client(site, token='x' * 40).repairs()


def test_admin_repair_routes(site, monkeypatch):
    import asyncio
    from fastapi import HTTPException
    from app.routes_admin import admin_repairs, admin_repair_update
    monkeypatch.setenv('PRINTSHOP_CLOUD_URL', site.base + '/shop.php')
    monkeypatch.setenv('PRINTSHOP_CLOUD_TOKEN', TOKEN)
    for name in ('CLOUD_URL', 'CLOUD_TOKEN'):
        monkeypatch.delattr(config, name, raising=False)
    book_repair(site)
    [r] = asyncio.run(admin_repairs(0))['repairs']
    out = asyncio.run(admin_repair_update(r['repair_id'], 'ready', '', '999.5', 0, 'ok'))
    assert out['final_amount'] == 999.5 and out['status'] == 'ready'
    with pytest.raises(HTTPException) as e:
        asyncio.run(admin_repair_update(r['repair_id'], 'collected', '', '', 0, ''))
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        asyncio.run(admin_repair_update(r['repair_id'], 'nope', '', '', 0, ''))
    assert e.value.status_code == 400
    out = asyncio.run(admin_repair_update(r['repair_id'], 'collected', '', '999.5', 1, ''))
    assert out['reward'] == 45


def test_admin_repairs_escape_customer_text():
    admin = (Path(__file__).resolve().parents[1] / 'static' / 'admin' / 'index.html').read_text()
    for needle in ("esc(r.name)", "esc(r.description)", "esc(r.brand)", "esc(r.model)", "esc(v.detail)", "esc(r.note || '')"):
        assert needle in admin, needle


# ---------------- green counter ----------------
def test_green_counter_counts_paper_saved_on_collected_orders(site, tmp_path):
    def stats():
        return httpx.get(site.base + '/stats.php').json()
    assert stats()['sheets_saved'] == 0
    # 10 pages, double-sided, 2 copies: 20 one-sided sheets -> 10 sheets: saves 10
    # 6-page file with 2 blank pages skipped (pages 1,3-4,6), 2 per sheet: 6 -> 2 sheets: saves 4
    o = place_order(site, [(pdf(tmp_path, 10), {**BW, 'doubleside': True, 'copies': 2}),
                           (pdf(tmp_path, 6, 'b.pdf'), {**BW, 'pages_range': '1,3-4,6', 'pages_per_sheet': 2, 'blank_skipped': 2, 'est_pages': 6})])
    s = collect(site, o, [20, 5])
    assert s['sheets_saved'] == 14
    st = stats()
    assert (st['sheets_saved'], st['orders']) == (14, 1) and st['paper_kg'] == round(14 * 4.99 / 1000, 1)
    # a cheeky blank count is capped by the file's page count, and ignored when all pages are printed
    o2 = place_order(site, [(pdf(tmp_path, 2, 'c.pdf'), {**BW, 'blank_skipped': 999, 'est_pages': 2})])
    assert collect(site, o2, [10])['sheets_saved'] == 0
    # not collected yet -> not in the public total
    o3 = place_order(site, [(pdf(tmp_path, 4, 'd.pdf'), {**BW, 'doubleside': True})])
    collect(site, o3, [10], pay=False)
    assert stats()['sheets_saved'] == 14


# ---------------- class / group orders ----------------
def make_group(site, expect=201, **over):
    body = {'title': 'S6 ECE · DSP lab record', 'rep_name': 'Rahul K', 'rep_phone': '9123456789', 'note': 'B&W double-sided'}
    body.update(over)
    r = httpx.post(site.base + '/group.php?action=create', json=body)
    assert r.status_code == expect, r.text
    return r.json()


def group_order(site, tmp_path, code, phone, name, expect=201):
    r = httpx.post(site.base + '/order.php', data={'name': name, 'phone': phone, 'group': code, 'options': json.dumps([BW])},
                   files=[('files[]', ('rec.pdf', pdf(tmp_path, 2).read_bytes(), 'application/pdf'))])
    assert r.status_code == expect, r.text
    return r.json()


def test_group_order_flow(site, tmp_path):
    g = make_group(site)
    assert re.fullmatch(r'G[A-Z0-9]{5}', g['code']) and len(g['admin']) == 32 and g['rep'] == 'Rahul'
    info = httpx.get(site.base + '/group.php', params={'action': 'info', 'code': g['code']}).json()
    assert info['title'] == 'S6 ECE · DSP lab record' and 'admin' not in info and 'rep_phone' not in info

    a = group_order(site, tmp_path, g['code'], '9876543210', 'Anu')
    group_order(site, tmp_path, g['code'].lower(), '9000000001', 'Binu')
    assert status(site, a)['group']['code'] == g['code']

    # staff: the job label carries the group, so search finds the whole class
    jobs = cloud_sync.sync_once(client(site))
    assert all(f"GROUP {g['code']} S6 ECE" in j['original_filename'] for j in jobs)
    print_service.confirm_payment(jobs[0]['id'], True)
    cloud_sync.push_statuses(client(site))

    m = httpx.get(site.base + '/group.php', params={'action': 'manage', 'code': g['code'], 'admin': g['admin']}).json()
    assert [(x['name'], x['paid']) for x in m['members']] == [('Anu', True), ('Binu', False)]
    assert all(x['phone'].count('*') == 6 for x in m['members'])
    assert httpx.get(site.base + '/group.php', params={'action': 'manage', 'code': g['code'], 'admin': '0' * 32}).status_code == 404

    # rep closes the group: no more joining
    assert httpx.post(site.base + '/group.php?action=close', json={'code': g['code'], 'admin': g['admin']}).json()['status'] == 'closed'
    group_order(site, tmp_path, g['code'], '9000000002', 'Chinnu', expect=400)
    assert httpx.post(site.base + '/group.php?action=close', json={'code': g['code'], 'admin': '1' * 32}).status_code == 404
    group_order(site, tmp_path, 'GZZZZZ', '9000000002', 'Chinnu', expect=400)


def test_group_validation(site):
    make_group(site, expect=400, title='')
    make_group(site, expect=400, rep_phone='123')
    make_group(site, expect=400, deadline='2001-01-01')
    assert httpx.get(site.base + '/group.php', params={'action': 'info', 'code': 'nope'}).status_code == 404



# ---------------- ready alerts (web push) ----------------
def test_ready_alert_is_sent_once_with_a_valid_vapid_signature(site, tmp_path):
    import base64
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    got = []

    class Push(BaseHTTPRequestHandler):
        def do_POST(self):
            got.append((self.path, dict(self.headers)))
            self.send_response(410 if self.path.endswith('/gone') else 201)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(('127.0.0.1', site.push_port), Push)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        ep = f'http://127.0.0.1:{site.push_port}/push/abc'
        o = place_order(site, [(pdf(tmp_path, 1), BW)])
        sub = lambda body: httpx.post(site.base + '/push.php?action=subscribe', json=body)
        assert sub({'code': o['code'], 'token': '0' * 32, 'endpoint': ep}).status_code == 404       # not the owner
        assert sub({'code': o['code'], 'token': o['token'], 'endpoint': 'https://evil.example.com/x'}).status_code == 400
        assert sub({'code': o['code'], 'token': o['token'], 'endpoint': 'http://10.0.0.1/x'}).status_code == 400
        assert sub({'code': o['code'], 'token': o['token'], 'endpoint': ep}).json()['status'] == 'pending'
        assert sub({'code': o['code'], 'token': o['token'], 'endpoint': ep[:-3] + 'gone'}).status_code == 200
        key = httpx.get(site.base + '/push.php?action=key').json()['key']

        [job] = cloud_sync.sync_once(client(site))
        print_service.confirm_payment(job['id'], True)
        cloud_sync.push_statuses(client(site))
        assert got == []                                    # paid is not ready
        db.update_job(job['id'], status='ready')
        cloud_sync.push_statuses(client(site))
        assert sorted(p for p, _ in got) == ['/push/abc', '/push/gone']

        h = next(hd for p, hd in got if p == '/push/abc')
        assert h['TTL'] == '86400' and h['Urgency'] == 'high'
        m = re.fullmatch(r'vapid t=([^,]+), k=(\S+)', h['Authorization'])
        jwt, k = m.group(1), m.group(2)
        assert k == key
        b64 = lambda x: base64.urlsafe_b64decode(x + '=' * (-len(x) % 4))
        head, claims, sig = jwt.split('.')
        assert json.loads(b64(claims))['aud'] == f'http://127.0.0.1:{site.push_port}'
        pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b64(k))
        raw = b64(sig)
        pub.verify(encode_dss_signature(int.from_bytes(raw[:32], 'big'), int.from_bytes(raw[32:], 'big')),
                   f'{head}.{claims}'.encode(), ec.ECDSA(hashes.SHA256()))   # raises if the signature is wrong

        # the service worker asks which order it is about
        which = httpx.post(site.base + '/push.php?action=which', json={'endpoint': ep}).json()
        assert which['orders'][0] == {'code': o['code'], 'status': 'ready'}
        # no second alert; the dead (410) subscription was removed
        got.clear()
        client(site).push_status([{'order_id': 1, 'status': 'ready', 'files': []}])
        assert got == []
        assert httpx.post(site.base + '/push.php?action=which', json={'endpoint': ep[:-3] + 'gone'}).json()['orders'] == []
    finally:
        srv.shutdown()


def test_backup_needs_the_shop_key_and_copies_every_table(site, tmp_path):
    place_order(site, [(pdf(tmp_path, 2), BW)])
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(site.base + '/shop.php?action=backup', timeout=5)
    assert e.value.code == 401
    hb = site.data / 'heartbeat'
    if hb.exists():
        hb.unlink()
    d = client(site)._json('GET', 'backup')
    assert not hb.exists()                      # a backup download is not a "server online" heartbeat
    assert len(d['tables']['orders']) == 1 and len(d['tables']['order_files']) == 1
    assert {'reward_ledger', 'repairs', 'print_groups', 'push_subs'} <= set(d['tables'])
    assert d['ledger']['ok'] is True
