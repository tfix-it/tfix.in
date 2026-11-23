# Changes

## 3.3.0 — Several shop PCs + T FIX AI PRINT
- Run many print agents at once (tested with 6). Each PC is identified by name, and only receives and can claim jobs
  for its own printers. Word/PowerPoint conversions are claimed by one PC, and re-offered if that PC stops mid-way.
  An offline PC blocks only its own printers. The admin lists connected PCs and shows which PC each printer is on.
  Database v2 adds an agents table and printer/conversion owner columns, migrated automatically.
- Customer header: T-PRINT headline with the sub-headline "T FIX AI PRINT"; the full name is now T FIX AI Print.
- The official T FIX store logo (vector redraw) replaces the earlier logo; new favicon from its triangle-T mark.

## 3.2.0 — T-PRINT · T FIX Auto Print
- New product name everywhere people see it: **T-PRINT** (full name *T FIX Auto Print*, tagline
  "Upload. Pay. Printed."). The customer page header has a T-PRINT wordmark whose hyphen is the brand's yellow bar.
- Page titles, the admin label, the UPI payment note (`T-PRINT #<job>`), the Claude connector/dashboard and the
  print agent use the new name. Internal names (TPRINT_* settings, tprint-config.php, database) are unchanged,
  so existing setups keep working.

## 3.1.0 — T FIX brand theme
- hostinger/assets/brand.css: the tfix.in brand system in one file (Lab Black, Graphite, T FIX Yellow for calls to
  action only, Off-white; Archivo / Inter / JetBrains Mono, self-hosted with their OFL licences; gold T FIX logo
  and favicon from the main site). No Google Fonts request any more.
- Customer page and admin restyled on it, in dark mode with the T FIX logo, mono eyebrows with the yellow bar and
  pill buttons. Selected options use off-white (like tfix.in's language switcher) so yellow stays for the main
  action. Print previews and the UPI QR stay white so they show paper and scan reliably.
- Claude dashboard: main action buttons in T FIX Yellow; the rest still follows Claude's light/dark theme.
- Fixed: admin job rows showed "No printers" until the queue changed. They now redraw when the printer list arrives.
- Fixed: admin login/setup CSS selectors broken in 3.0.0 (styles leaked from the setup screen onto the login box).

## 3.0.0 — Hostinger only: print.tfix.in + print agent + UPI QR
- hostinger/: the whole online side in PHP 8.2+ and MySQL for Hostinger shared hosting. It has the customer
  order site, the full admin at /admin/ (same API paths as the LAN admin), and the Claude connector at /mcp (same
  six tools and MCP App dashboard). The schema is created automatically. Private config and uploaded files live
  outside public_html.
- UPI payment QR after submitting an order: exact total, `TPrint #<job>` note, a "Pay with a UPI app" deep
  link, and automatic switch to "Payment received". The UPI ID and payee name are set in the admin's System settings.
- agent/print_agent.py: the shop PC polls print.tfix.in over HTTPS (no inbound connections), reports
  printers, claims and prints dispatched jobs with SumatraPDF (never retrying an uncertain print) and
  converts Word/PowerPoint with LibreOffice.
- Print gate kept: payment confirmed by staff, queued, enabled printer, file present, one dispatch per job ever;
  the agent's claim is atomic; pending dispatches can be cancelled. The gate refuses while the shop PC is offline.
- Hardening for the public internet:
  - pages counted on the server (pure-PHP PDF parser, verified against PyMuPDF)
  - customers see their orders only through private tokens
  - admin page escapes customer-supplied text (stored-XSS fix) and file names are sanitised
  - Origin checks on state-changing requests
  - DB-backed rate limits
  - one-time setup code instead of a default password
- Fixed: on the customer status screen, the phone/submit section could reappear after sending.
- Tests: tests/hostinger runs the PHP site under `php -S` against MariaDB: API, pricing parity with app/pricing.py,
  agent and connector (40 tests).

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
