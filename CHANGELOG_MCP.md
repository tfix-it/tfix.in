# Changes

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
