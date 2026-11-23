"""Behaviour of the PHP/MySQL version that runs at https://print.tfix.in."""
import itertools

import pytest

from .conftest import (AGENT_KEY, ORIGIN, SETUP_CODE, agent_poll, make_pdf, sql, submit, upload)


# ------------------------------------------------------------------ pricing parity
def test_pricing_matches_original_python(client, tmp_path, monkeypatch):
    """Every combination prices exactly like app/pricing.py (the shop's pdfcostcalc rules)."""
    from app import config, db, pricing
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "p.db")
    monkeypatch.setattr(config, "INITIAL_ADMIN_PASSWORD", "x")
    db.init_db()
    for pages, pps, color, duplex, copies, size, first in itertools.product(
            (1, 2, 3, 7, 16), (1, 2, 4), (False, True), (False, True), (1, 3), ("A4", "A3"), (True, False)):
        want_cost, want_sheets = pricing.print_cost(pages, pps, color, duplex, copies, size, apply_min_cost=first)
        phone = "" if first else "1111111111"
        if not first:
            sql("DELETE FROM jobs") if sql("SHOW TABLES LIKE 'jobs'") else None
        r = client.post("/api/quote", data={"pages": pages, "pages_per_sheet": pps, "color": str(color).lower(),
                                            "doubleside": str(duplex).lower(), "copies": copies, "paper_size": size,
                                            "phone": phone, "files_ahead_today": 0 if first else 1})
        assert r.status_code == 200, r.text
        got = r.json()
        assert (round(got["cost"], 2), got["sheets"]) == (round(want_cost, 2), want_sheets), (pages, pps, color, duplex, copies, size, first)


# ------------------------------------------------------------------ uploads
def test_pdf_upload_counts_pages_on_server(client):
    r = upload(client, data=make_pdf(5))
    assert r.status_code == 200, r.text
    u = r.json()
    assert u["pages"] == 5 and u["status"] == "ready" and len(u["upload_id"]) == 32
    preview = client.get(f"/api/uploads/{u['upload_id']}/pdf")
    assert preview.status_code == 200 and preview.content.startswith(b"%PDF")


def test_image_upload_becomes_one_page_pdf(client):
    import fitz
    pm = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 60, 40), 1)
    pm.clear_with(128)
    for name, data, ctype in (("photo.png", pm.tobytes("png"), "image/png"),
                              ("photo.jpg", fitz.Pixmap(pm, 0).tobytes("jpeg"), "image/jpeg")):
        r = upload(client, name, data, ctype)
        assert r.status_code == 200, r.text
        pdf = client.get(f"/api/uploads/{r.json()['upload_id']}/pdf").content
        with fitz.open(stream=pdf, filetype="pdf") as doc:
            assert doc.page_count == 1 and doc[0].get_images()


@pytest.mark.parametrize("name,data,status,msg", [
    ("virus.exe", b"MZ", 400, "Unsupported file type"),
    ("fake.pdf", b"hello", 400, "valid PDF"),
    ("notimage.png", b"nope", 400, "image"),
])
def test_bad_uploads_rejected(client, name, data, status, msg):
    r = upload(client, name, data)
    assert r.status_code == status and msg in r.json()["detail"]


def test_upload_size_limit_and_rate_limit(client, admin):
    admin.post("/api/admin/settings", data={"max_upload_mb": 1, "rate_limit_max_requests": 3, "rate_limit_window_sec": 600})
    big = make_pdf(1) + b"%" + b"x" * (1024 * 1024 + 10)
    assert upload(client, data=big).status_code == 413
    assert upload(client).status_code == 200
    assert upload(client).status_code == 200
    r = upload(client)
    assert r.status_code == 429 and "wait" in r.json()["detail"]
    # A different visitor is not affected.
    client.headers["X-Forwarded-For"] = "198.51.100.7"
    assert upload(client).status_code == 200


# ------------------------------------------------------------------ jobs
def test_job_uses_server_page_count_and_range(client):
    up = upload(client, data=make_pdf(10)).json()
    r = submit(client, up["upload_id"], pages=1, pages_range="2-4,9", copies=2)
    assert r.status_code == 200, r.text
    job = r.json()
    row = sql("SELECT pages, pages_range, sheets, cost FROM jobs WHERE id=?", [job["id"]])[0]
    assert row["pages"] == 4 and row["pages_range"] == "2-4,9"   # browser said 1 page: ignored
    assert row["sheets"] == 8 and float(row["cost"]) == 4 * 2 * 2 - 2 + 10   # first job of the day: B&W minimum
    assert submit(client, up["upload_id"], pages_range="9-12").status_code == 400


def test_daily_sequence_and_minimum_waiver(client):
    up = upload(client, data=make_pdf(1)).json()
    first = submit(client, up["upload_id"]).json()
    second = submit(client, up["upload_id"]).json()
    other = submit(client, up["upload_id"], phone="9000000001").json()
    assert (first["daily_seq"], second["daily_seq"], other["daily_seq"]) == (1, 2, 1)
    assert (first["cost"], second["cost"], other["cost"]) == (10.0, 2.0, 10.0)


def test_customer_sees_only_own_jobs_by_token(client):
    up = upload(client).json()
    job = submit(client, up["upload_id"]).json()
    assert set(job) >= {"id", "token", "status", "cost", "paid"}
    assert not {"pdf_file", "upload_id", "pdf_filename", "source_file"} & set(job)
    r = client.get("/api/jobs/status", params={"tokens": job["token"] + ",deadbeef," + "0" * 32})
    assert [j["id"] for j in r.json()] == [job["id"]]
    for path in (f"/api/jobs/{job['id']}", "/api/jobs/by-phone/9876543210", f"/api/admin/jobs"):
        assert client.get(path).status_code in (401, 404), path


@pytest.mark.parametrize("field,value", [("phone", "12345"), ("paper_size", "A0"), ("orientation", "upside"),
                                         ("pages_per_sheet", 3), ("copies", 0)])
def test_job_validation(client, field, value):
    up = upload(client).json()
    assert submit(client, up["upload_id"], **{field: value}).status_code == 400


def test_payment_info_for_qr(client, admin):
    assert client.get("/api/payment-info").json() == {"upi_id": "", "upi_payee_name": ""}
    bad = admin.post("/api/admin/settings", data={"upi_id": "not an id"})
    assert bad.status_code == 400
    ok = admin.post("/api/admin/settings", data={"upi_id": "tfixprint@okaxis", "upi_payee_name": "TFix Print"})
    assert ok.status_code == 200, ok.text
    assert client.get("/api/payment-info").json() == {"upi_id": "tfixprint@okaxis", "upi_payee_name": "TFix Print"}


# ------------------------------------------------------------------ admin auth
def test_first_time_setup_then_login(server, client):
    client.headers["Origin"] = ORIGIN
    assert client.get("/api/admin/setup-status").json() == {"setup_required": True}
    assert client.post("/api/admin/login", data={"username": "admin", "password": "x"}).json()["detail"] == "setup_required"
    assert client.post("/api/admin/setup", data={"setup_code": "wrong", "new_password": "longenough1"}).status_code == 401
    assert client.post("/api/admin/setup", data={"setup_code": SETUP_CODE, "new_password": "short"}).status_code == 400
    assert client.post("/api/admin/setup", data={"setup_code": SETUP_CODE, "new_password": "longenough1"}).status_code == 200
    assert client.post("/api/admin/setup", data={"setup_code": SETUP_CODE, "new_password": "takeover123"}).status_code == 409
    client.headers["X-Forwarded-For"] = "203.0.113.99"
    r = client.post("/api/admin/login", data={"username": "admin", "password": "longenough1"})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "secure" in cookie.lower() and "SameSite=Lax" in cookie


def test_login_rate_limit(client, admin):
    client.headers["Origin"] = ORIGIN
    codes = [client.post("/api/admin/login", data={"username": "admin", "password": "nope"}).status_code for _ in range(6)]
    assert codes == [401] * 5 + [429]


def test_admin_requires_session_and_same_origin(client, admin):
    assert client.get("/api/admin/jobs").status_code == 401
    up = upload(client).json()
    job = submit(client, up["upload_id"]).json()
    evil = admin.post(f"/api/admin/jobs/{job['id']}/paid", data={"paid": "true"}, headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403
    assert sql("SELECT paid FROM jobs WHERE id=?", [job["id"]])[0]["paid"] == 0


def test_change_password_signs_everyone_out(admin):
    r = admin.post("/api/admin/change-password", data={"current_password": "correct-horse-battery", "new_password": "another-long-pass"})
    assert r.status_code == 200
    assert admin.get("/api/admin/jobs").status_code == 401


# ------------------------------------------------------------------ print gate + agent
def _paid_job(client, admin, **opts):
    up = upload(client, data=make_pdf(2)).json()
    job = submit(client, up["upload_id"], **opts).json()
    r = admin.post(f"/api/admin/jobs/{job['id']}/paid", data={"paid": "true"})
    assert r.status_code == 200, r.text
    return r.json()


def test_full_print_flow_through_agent(client, admin, agent):
    job = _paid_job(client, admin)
    offline = admin.post(f"/api/admin/jobs/{job['id']}/print", data={"printer_name": "HP LaserJet"})
    assert offline.status_code == 409 and "not registered" in offline.json()["detail"]   # no shop PC has connected yet
    agent_poll(agent)
    assert admin.get("/api/admin/agent").json()["online"] is True
    printers = admin.get("/api/admin/printers").json()
    assert {p["windows_printer_name"]: p["live_status"] for p in printers} == {"HP LaserJet": "ready", "Epson Colour": "paper_out"}

    r = admin.post(f"/api/admin/jobs/{job['id']}/print", data={"printer_name": "HP LaserJet"})
    assert r.status_code == 200 and r.json()["status"] == "printing" and r.json()["dispatch"]["state"] == "pending"
    again = admin.post(f"/api/admin/jobs/{job['id']}/print", data={"printer_name": "HP LaserJet"})
    assert again.status_code == 409

    work = agent_poll(agent)
    assert [d["job_id"] for d in work["dispatches"]] == [job["id"]]
    assert agent.get(f"/api/agent/jobs/{job['id']}/file").status_code == 409          # must claim first
    assert agent.post(f"/api/agent/dispatches/{job['id']}/claim").status_code == 200
    assert agent.post(f"/api/agent/dispatches/{job['id']}/claim").status_code == 409  # never twice
    assert admin.post(f"/api/admin/jobs/{job['id']}/cancel-print").status_code == 409  # too late to cancel
    pdf = agent.get(f"/api/agent/jobs/{job['id']}/file")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert agent.post(f"/api/agent/dispatches/{job['id']}/result", json={"ok": True}).json() == {"ok": True}
    assert agent_poll(agent)["dispatches"] == []
    view = [j for j in admin.get("/api/admin/jobs").json() if j["id"] == job["id"]][0]
    assert view["dispatch"]["state"] == "submitted"
    # Payment can't be revoked after dispatch.
    assert admin.post(f"/api/admin/jobs/{job['id']}/paid", data={"paid": "false"}).status_code == 409


def test_unpaid_disabled_printer_and_cancel(client, admin, agent):
    agent_poll(agent)
    up = upload(client).json()
    unpaid = submit(client, up["upload_id"]).json()
    r = admin.post(f"/api/admin/jobs/{unpaid['id']}/print", data={"printer_name": "HP LaserJet"})
    assert r.status_code == 409 and "Payment" in r.json()["detail"]
    job = _paid_job(client, admin)
    pid = [p["id"] for p in admin.get("/api/admin/printers").json() if p["windows_printer_name"] == "HP LaserJet"][0]
    admin.post(f"/api/admin/printers/{pid}/enabled", data={"enabled": "false"})
    assert admin.post(f"/api/admin/jobs/{job['id']}/print", data={"printer_name": "HP LaserJet"}).status_code == 409
    admin.post(f"/api/admin/printers/{pid}/enabled", data={"enabled": "true"})
    assert admin.post(f"/api/admin/jobs/{job['id']}/print", data={"printer_name": "HP LaserJet"}).status_code == 200
    back = admin.post(f"/api/admin/jobs/{job['id']}/cancel-print")
    assert back.status_code == 200 and back.json()["status"] == "queued" and back.json()["dispatch"] is None
    assert agent.post(f"/api/agent/dispatches/{job['id']}/claim").status_code == 409


def test_uncertain_print_is_never_retried(client, admin, agent):
    agent_poll(agent)
    job = _paid_job(client, admin)
    admin.post(f"/api/admin/jobs/{job['id']}/print", data={"printer_name": "HP LaserJet"})
    agent.post(f"/api/agent/dispatches/{job['id']}/claim")
    agent.post(f"/api/agent/dispatches/{job['id']}/result", json={"ok": False, "error": "SumatraPDF timed out"})
    admin.post(f"/api/admin/jobs/{job['id']}/status", data={"status": "queued"})
    r = admin.post(f"/api/admin/jobs/{job['id']}/print", data={"printer_name": "HP LaserJet"})
    assert r.status_code == 409 and "do not retry" in r.json()["detail"]
    view = [j for j in admin.get("/api/admin/jobs").json() if j["id"] == job["id"]][0]
    assert view["dispatch"] == {"state": "uncertain", "printer": "HP LaserJet", "error": "SumatraPDF timed out",
                                "updated_at": view["dispatch"]["updated_at"]}


def test_auto_print_after_staff_payment(client, admin, agent):
    agent_poll(agent)
    admin.post("/api/admin/auto-print", data={"enabled": "true", "printer_name": "HP LaserJet", "bw_only": "true",
                                              "max_sheets": "20", "max_copies": "5"})
    up = upload(client).json()
    job = submit(client, up["upload_id"]).json()
    assert job["status"] == "queued"                           # unpaid: never auto-printed
    paid = admin.post(f"/api/admin/jobs/{job['id']}/paid", data={"paid": "true"}).json()
    assert paid["status"] == "printing" and paid["auto_printed"] == 1 and paid["dispatch"]["state"] == "pending"
    colour = _paid_job(client, admin, color="true")
    assert colour["status"] == "queued"                        # rule: B&W only
    audit = sql("SELECT actor FROM payment_audit WHERE job_id=?", [job["id"]])
    assert audit == [{"actor": "staff_manual"}]


def test_agent_requires_key(client, server):
    import httpx
    for headers in ({}, {"Authorization": "Bearer wrong"}):
        assert httpx.post(server.base + "/api/agent/poll", json={}, headers=headers).status_code == 401


def test_admin_file_download_is_guarded(client, admin):
    up = upload(client).json()
    job = submit(client, up["upload_id"]).json()
    assert client.get(f"/api/admin/jobs/{job['id']}/file").status_code == 401
    r = admin.get(f"/api/admin/jobs/{job['id']}/file")
    assert r.status_code == 200 and r.content.startswith(b"%PDF") and r.headers["content-type"] == "application/pdf"


def test_private_folders_not_served(client):
    for path in ("/lib/core.php", "/lib/bootstrap.php", "/lib/mcp_dashboard.html"):
        assert client.get(path).status_code == 403, path


# ------------------------------------------------------------------ Word/PowerPoint via the agent
def test_docx_converted_by_agent(client, agent):
    r = upload(client, "letter.docx", b"PK\x03\x04 fake docx", "application/octet-stream")
    up = r.json()
    assert r.status_code == 200 and up["status"] == "converting" and up["shop_pc_online"] is False
    assert submit(client, up["upload_id"]).status_code == 400              # not ready yet
    work = agent_poll(agent)
    assert work["conversions"] == [{"upload_id": up["upload_id"], "file_type": "docx"}]
    src = agent.get(f"/api/agent/conversions/{up['upload_id']}/source")
    assert src.content == b"PK\x03\x04 fake docx"
    done = agent.post(f"/api/agent/conversions/{up['upload_id']}/result", files={"pdf": ("out.pdf", make_pdf(6), "application/pdf")})
    assert done.json() == {"ok": True}
    status = client.get(f"/api/uploads/{up['upload_id']}").json()
    assert status["status"] == "ready" and status["pages"] == 6
    assert submit(client, up["upload_id"]).status_code == 200


def test_failed_conversion_reported(client, agent):
    up = upload(client, "deck.pptx", b"PK junk", "application/octet-stream").json()
    agent.post(f"/api/agent/conversions/{up['upload_id']}/result", data={"error": "LibreOffice could not open the file"})
    status = client.get(f"/api/uploads/{up['upload_id']}").json()
    assert status["status"] == "failed" and "LibreOffice" in status["error"]


# ------------------------------------------------------------------ cleanup
def test_unpaid_files_cleaned_up(client, admin, server):
    up = upload(client).json()
    job = submit(client, up["upload_id"]).json()
    sql("UPDATE jobs SET created_at = created_at - INTERVAL 5 DAY WHERE id=?", [job["id"]])
    r = admin.post("/api/admin/settings/run-cleanup-now")
    assert r.json() == {"ok": True, "removed": 1}
    view = [j for j in admin.get("/api/admin/jobs").json() if j["id"] == job["id"]][0]
    assert view["files_deleted"] == 1 and not any(server.storage.iterdir())
    assert admin.get(f"/api/admin/jobs/{job['id']}/file").status_code == 404


# ------------------------------------------------------------------ several shop PCs at once
def test_several_agents_each_print_only_their_own_printers(client, admin, agent):
    pcs = {f"counter-{i}": f"Printer {i}" for i in range(1, 7)}           # six shop PCs, one printer each
    admin.post("/api/admin/settings", data={"rate_limit_max_requests": 100})  # 6 uploads + 6 orders from one test client
    for pc, printer in pcs.items():
        agent_poll(agent, printers=((printer, "ready"),), name=pc)
    status = admin.get("/api/admin/agent").json()
    assert status["online_count"] == 6 and {a["name"] for a in status["agents"]} == set(pcs)
    jobs = {}
    for pc, printer in pcs.items():
        job = _paid_job(client, admin)
        assert admin.post(f"/api/admin/jobs/{job['id']}/print", data={"printer_name": printer}).status_code == 200
        jobs[pc] = job["id"]
    for pc, printer in pcs.items():
        work = agent_poll(agent, printers=((printer, "ready"),), name=pc)
        assert [d["job_id"] for d in work["dispatches"]] == [jobs[pc]]          # only its own job
        other = jobs["counter-1" if pc != "counter-1" else "counter-2"]
        hdr = {"X-TPrint-Agent": pc}
        assert agent.post(f"/api/agent/dispatches/{other}/claim", headers=hdr).status_code == 409   # can't take another PC's job
        assert agent.post(f"/api/agent/dispatches/{jobs[pc]}/claim", headers=hdr).status_code == 200


def test_offline_pc_blocks_only_its_printers(client, admin, agent):
    agent_poll(agent, printers=(("Front HP", "ready"),), name="front")
    agent_poll(agent, printers=(("Back Epson", "ready"),), name="back")
    sql("UPDATE agents SET last_seen = last_seen - INTERVAL 10 MINUTE WHERE name='back'")
    printers = {p["windows_printer_name"]: p for p in admin.get("/api/admin/printers").json()}
    assert printers["Front HP"]["live_status"] == "ready" and printers["Back Epson"]["live_status"] == "offline"
    a, b = _paid_job(client, admin), _paid_job(client, admin)
    assert admin.post(f"/api/admin/jobs/{a['id']}/print", data={"printer_name": "Front HP"}).status_code == 200
    r = admin.post(f"/api/admin/jobs/{b['id']}/print", data={"printer_name": "Back Epson"})
    assert r.status_code == 409 and "back" in r.json()["detail"]


def test_conversion_claimed_by_one_pc_only(client, agent):
    up = upload(client, "notes.docx", b"PK fake", "application/octet-stream").json()
    a = agent.post(f"/api/agent/conversions/{up['upload_id']}/claim", headers={"X-TPrint-Agent": "pc-a"})
    b = agent.post(f"/api/agent/conversions/{up['upload_id']}/claim", headers={"X-TPrint-Agent": "pc-b"})
    assert (a.status_code, b.status_code) == (200, 409)
    assert agent_poll(agent, name="pc-c")["conversions"] == []                 # claimed: not offered again
    sql("UPDATE uploads SET conv_claimed_at = conv_claimed_at - INTERVAL 5 MINUTE")
    assert [c["upload_id"] for c in agent_poll(agent, name="pc-c")["conversions"]] == [up["upload_id"]]   # stale claim re-offered
