"""The Claude connector at https://print.tfix.in/mcp (PHP version)."""
from pathlib import Path

import httpx
import pytest

from .conftest import MCP_TOKEN, REPO, agent_poll, make_pdf, sql, submit, upload

HEADERS = {"Authorization": "Bearer " + MCP_TOKEN, "Accept": "application/json, text/event-stream",
           "MCP-Protocol-Version": "2025-11-25"}


def rpc(server, method, params=None, rid=1, headers=HEADERS):
    body = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        body["params"] = params
    r = httpx.post(server.base + "/mcp", json=body, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def call(server, name, args=None):
    return rpc(server, "tools/call", {"name": name, "arguments": args or {}})["result"]


def test_auth_and_transport(server):
    assert httpx.post(server.base + "/mcp", json={}).status_code == 401
    assert httpx.post(server.base + "/mcp", json={}, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert httpx.get(server.base + "/mcp", headers=HEADERS).status_code == 405
    bad = dict(HEADERS, **{"MCP-Protocol-Version": "1999-01-01"})
    assert httpx.post(server.base + "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers=bad).status_code == 400
    note = httpx.post(server.base + "/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=HEADERS)
    assert note.status_code == 202
    server.write_config(mcp_token="")
    assert httpx.post(server.base + "/mcp", json={}, headers=HEADERS).status_code == 404


def test_initialize_and_discovery(server):
    init = rpc(server, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                      "clientInfo": {"name": "test", "version": "1"}})["result"]
    assert init["protocolVersion"] == "2025-11-25" and init["serverInfo"]["name"] == "TPrint"
    older = rpc(server, "initialize", {"protocolVersion": "2025-03-26"})["result"]
    assert older["protocolVersion"] == "2025-03-26"
    tools = {t["name"]: t for t in rpc(server, "tools/list")["result"]["tools"]}
    assert set(tools) == {"open_print_dashboard", "list_print_jobs", "list_printers", "get_pricing",
                          "get_auto_print_settings", "print_paid_job"}
    assert tools["open_print_dashboard"]["_meta"]["ui"]["resourceUri"] == "ui://tprint/dashboard.html"
    res = rpc(server, "resources/list")["result"]["resources"]
    assert res[0]["mimeType"] == "text/html;profile=mcp-app"
    html = rpc(server, "resources/read", {"uri": "ui://tprint/dashboard.html"})["result"]["contents"][0]
    assert html["text"] == (REPO / "app/mcp_ui/dashboard.html").read_text(encoding="utf-8")
    assert rpc(server, "nope/method")["error"]["code"] == -32601
    assert rpc(server, "ping")["result"] == {}


def test_dashboard_snapshot_never_leaks_customer_identity(server, client):
    up = upload(client, "secret-thesis.pdf", make_pdf(4)).json()
    submit(client, up["upload_id"], phone="9123456789")
    r = call(server, "open_print_dashboard", {"status": "queued"})
    snap, text = r["structuredContent"], r["content"][0]["text"]
    assert snap["counts"]["queued"] == 1 and snap["counts"]["awaiting_payment"] == 1
    assert snap["jobs"][0]["pages"] == 4 and snap["printing_enabled"] is False
    assert snap["admin_url"] == "https://print.tfix.in/admin/" and snap["shop_pc_online"] is False
    for leaked in ("9123456789", "secret-thesis"):
        assert leaked not in str(snap) and leaked not in text
    for name in ("list_print_jobs", "list_printers", "get_pricing", "get_auto_print_settings"):
        out = call(server, name)
        assert not out.get("isError") and "9123456789" not in str(out)
    bad = call(server, "open_print_dashboard", {"status": "nope"})
    assert bad["isError"] and "status must be one of" in bad["content"][0]["text"]


def test_print_from_claude(server, client, admin, agent):
    up = upload(client).json()
    job = submit(client, up["upload_id"]).json()
    args = {"job_id": job["id"], "printer_name": "HP LaserJet", "confirm": True}
    off = call(server, "print_paid_job", args)
    assert off["isError"] and "disabled" in off["content"][0]["text"]
    server.write_config(mcp_allow_print=True)
    assert "confirmation" in call(server, "print_paid_job", dict(args, confirm=False))["content"][0]["text"]
    agent_poll(agent)
    unpaid = call(server, "print_paid_job", args)
    assert unpaid["isError"] and "Payment must be confirmed" in unpaid["content"][0]["text"]
    admin.post(f"/api/admin/jobs/{job['id']}/paid", data={"paid": "true"})
    ok = call(server, "print_paid_job", args)
    assert not ok.get("isError") and '"status": "printing"' in ok["content"][0]["text"]
    again = call(server, "print_paid_job", args)
    assert again["isError"] and "Only queued" in again["content"][0]["text"]
    assert sql("SELECT actor FROM print_dispatches WHERE job_id=?", [job["id"]]) == [{"actor": "claude_mcp"}]


def test_dashboard_copies_identical():
    assert (REPO / "hostinger/lib/mcp_dashboard.html").read_bytes() == (REPO / "app/mcp_ui/dashboard.html").read_bytes()
