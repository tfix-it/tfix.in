# Changes

## 2.1.0 — Full shop admin online at print.tfix.in
- app/public_server.py: the connector listener (127.0.0.1:8765) also serves the existing admin page and
  admin API (routes_admin.py) on every path except /mcp, when TPRINT_REMOTE_ADMIN=1. It adds session-guarded
  job-file downloads (/uploads/<name>), a Host allow-list, an Origin check on state-changing requests and
  WebSocket handshakes, and no API docs. The customer site and API are never published.
- deploy/cloudflared-config.yml: named-tunnel ingress for print.tfix.in. /mcp uses the bearer token; everything
  else requires a Cloudflare Access login, which cloudflared itself enforces (originRequest.access.required).
- app/job_watch.py: each process (LAN server, connector) polls the jobs table for changes and broadcasts them,
  so the LAN admin, the online admin and customer status pages update each other live.
- uvicorn trusts X-Forwarded-For from cloudflared on 127.0.0.1, so the admin login rate limit is per visitor.
- maybe_auto_print moved to print_service (re-exported from routes_customer) so the online admin does not load
  the upload/conversion stack.
- Claude dashboard: "Full admin" button opens https://print.tfix.in when the online admin is enabled.
- requirements-mcp.txt: + fastapi 0.165.0, uvicorn 0.54.0, websockets 17.4 (without websockets the live admin
  updates silently fail to connect).

## 2.0.0 — TPrint as an MCP App
- app/mcp_server.py: ported from FastMCP (SDK 1.26) to `mcp.server.mcpserver.MCPServer` (SDK 2.3.0) with the
  MCP Apps extension (`mcp.server.apps.Apps`).
- New `open_print_dashboard` tool bound to the `ui://tprint/dashboard.html` resource
  (`text/html;profile=mcp-app`). Returns a plain-text summary for the model and structured data for the UI.
- app/mcp_ui/dashboard.html: self-contained dashboard (no external requests) speaking the MCP Apps
  postMessage protocol 2026-01-26: host theming and fonts, auto-resize, refresh, status tabs, printer
  status, pricing/auto-print view, and a two-step Print > Confirm flow that calls `print_paid_job`.
  After a print it reports the action to Claude via `ui/update-model-context`.
- Expected failures (bad arguments, printing disabled, missing confirmation, every print-gate refusal)
  are raised as `ToolError`, so their reason reaches Claude and the dashboard. SDK 2.x hides the text of
  any other exception.
- New `--transport stdio` mode for Claude Desktop on the shop PC (no listener, no token). HTTP mode is
  unchanged: loopback-only, bearer token, host validation, stateless JSON responses.
- requirements-mcp.txt: mcp==2.3.0 and pywin32==311 (mcp 2.3 requires pywin32>=311 on Windows).
- tests: MCP tests use `create_http_app`; new checks for the ui:// resource, dashboard data and the
  privacy rule that phone numbers and filenames never leave the PC.

## 1.0.0
- app/print_service.py: durable single-dispatch gate, payment audit and shared printer/file/payment validation.
- app/main.py: additive audit-table initialization at startup.
- app/routes_admin.py: print via shared gate; staff Paid action audits confirmation and evaluates auto-print.
- app/routes_customer.py: unpaid creation cannot auto-print; eligible paid jobs use shared gate.
- app/mcp_server.py: independent loopback-only authenticated Streamable HTTP MCP listener; five narrowly scoped tools; remote printing disabled by default.
- requirements-mcp.txt: separate environment for SDK compatibility.
- tests/test_safety.py: isolated safety and protocol tests; no physical printer access.
- START_HERE.md: backup, Windows setup, HTTPS exposure, Claude settings and recovery limitations.
