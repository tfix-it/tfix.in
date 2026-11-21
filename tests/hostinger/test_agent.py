"""The shop-PC print agent against the real PHP server (fake printer and LibreOffice)."""
from pathlib import Path

import httpx
import pytest

from agent.print_agent import Agent
from .conftest import AGENT_KEY, make_pdf, submit, upload


class FakePrinting:
    def __init__(self, fail=None):
        self.printed = []
        self.fail = fail

    def list_windows_printers(self):
        return ["HP LaserJet", "Epson Colour"]

    def get_printer_status(self, name):
        return {"HP LaserJet": "ready", "Epson Colour": "paper_out"}[name]

    def print_pdf(self, path, printer, duplex, copies, **kw):
        assert Path(path).read_bytes().startswith(b"%PDF")
        self.printed.append((printer, duplex, copies, kw))
        if self.fail:
            raise self.fail


class FakeConverter:
    def __init__(self, pages=3, fail=False):
        self.pages, self.fail = pages, fail

    def convert(self, source):
        if self.fail:
            raise RuntimeError("soffice crashed")
        out = source.with_suffix(".pdf")
        out.write_bytes(make_pdf(self.pages))
        return out


def make_agent(server, printing=None, converter=None):
    return Agent(server.base, AGENT_KEY, printing or FakePrinting(), converter or FakeConverter())


def paid_job(client, admin, **opts):
    up = upload(client, data=make_pdf(4)).json()
    job = submit(client, up["upload_id"], **opts).json()
    admin.post(f"/api/admin/jobs/{job['id']}/paid", data={"paid": "true"})
    return job


def job_view(admin, job_id):
    return [j for j in admin.get("/api/admin/jobs").json() if j["id"] == job_id][0]


def test_agent_prints_exactly_once_with_job_options(server, client, admin):
    printing = FakePrinting()
    a = make_agent(server, printing)
    a.run_once()                                   # registers printers, marks the shop PC online
    job = paid_job(client, admin, doubleside="true", duplex_edge="short", copies=2, orientation="landscape", pages_range="2-3")
    assert admin.post(f"/api/admin/jobs/{job['id']}/print", data={"printer_name": "HP LaserJet"}).status_code == 200
    a.run_once()
    a.run_once()                                   # nothing left to do: no second print
    assert printing.printed == [("HP LaserJet", True, 2, {"orientation": "landscape", "duplex_edge": "short",
                                                          "pages_range": "2-3", "scale_mode": "fit"})]
    assert job_view(admin, job["id"])["dispatch"]["state"] == "submitted"


def test_print_failure_is_uncertain_and_not_retried(server, client, admin):
    printing = FakePrinting(fail=TimeoutError("SumatraPDF timed out"))
    a = make_agent(server, printing)
    a.run_once()
    job = paid_job(client, admin)
    admin.post(f"/api/admin/jobs/{job['id']}/print", data={"printer_name": "HP LaserJet"})
    a.run_once()
    a.run_once()
    assert len(printing.printed) == 1
    d = job_view(admin, job["id"])["dispatch"]
    assert d["state"] == "uncertain" and "timed out" in d["error"]


def test_download_failure_returns_job_to_queue(server, client, admin, monkeypatch):
    printing = FakePrinting()
    a = make_agent(server, printing)
    a.run_once()
    job = paid_job(client, admin)
    admin.post(f"/api/admin/jobs/{job['id']}/print", data={"printer_name": "HP LaserJet"})
    real_get = a.http.get
    monkeypatch.setattr(a.http, "get", lambda url, **kw: (_ for _ in ()).throw(httpx.ReadTimeout("slow"))
                        if "/file" in url else real_get(url, **kw))
    a.run_once()
    assert printing.printed == []
    v = job_view(admin, job["id"])
    assert v["status"] == "queued" and v["dispatch"] is None
    monkeypatch.setattr(a.http, "get", real_get)
    admin.post(f"/api/admin/jobs/{job['id']}/print", data={"printer_name": "HP LaserJet"})
    a.run_once()
    assert len(printing.printed) == 1


def test_agent_converts_office_files(server, client):
    a = make_agent(server, converter=FakeConverter(pages=8))
    up = upload(client, "notes.docx", b"PK fake", "application/octet-stream").json()
    a.run_once()
    st = client.get(f"/api/uploads/{up['upload_id']}").json()
    assert st["status"] == "ready" and st["pages"] == 8
    bad = make_agent(server, converter=FakeConverter(fail=True))
    up2 = upload(client, "slides.pptx", b"PK fake", "application/octet-stream").json()
    bad.run_once()
    st2 = client.get(f"/api/uploads/{up2['upload_id']}").json()
    assert st2["status"] == "failed" and "PDF" in st2["error"]


def test_wrong_key_is_rejected(server):
    a = Agent(server.base, "x" * 40, FakePrinting(), FakeConverter())
    with pytest.raises(httpx.HTTPStatusError) as e:
        a.run_once()
    assert e.value.response.status_code == 401
