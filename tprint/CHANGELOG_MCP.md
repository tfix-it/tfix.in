# Changes
- app/print_service.py: durable single-dispatch gate, payment audit and shared printer/file/payment validation.
- app/main.py: additive audit-table initialization at startup.
- app/routes_admin.py: print via shared gate; staff Paid action audits confirmation and evaluates auto-print.
- app/routes_customer.py: unpaid creation cannot auto-print; eligible paid jobs use shared gate.
- app/mcp_server.py: independent loopback-only authenticated Streamable HTTP MCP listener; five narrowly scoped tools; remote printing disabled by default.
- requirements-mcp.txt: separate environment for SDK compatibility.
- tests/test_safety.py: isolated safety and protocol tests; no physical printer access.
- START_HERE.md: backup, Windows setup, HTTPS exposure, Claude settings and recovery limitations.

# Customer API hardening (on top of the MCP edition)
- Page count is read from the uploaded PDF on the server; the page range is
  checked against it. The browser's `pages` value is ignored, so a 50-page
  file can't be priced as 1 page.
- Upload names in a new job must be a plain file inside `uploads/` (no
  folders, `..` or absolute paths), and the print file must be a PDF.
- Copies must be 1–500; pages per sheet must be 1, 2, 4, 6, 9 or 16
  (stops negative prices and divide-by-zero).
- Public lookups (`GET /api/jobs/{id}`, `/api/jobs/by-phone/{phone}`, the
  customer WebSocket) no longer return phone numbers or file names, so
  walking job IDs can't reveal customers or link to their documents. The
  customer status page keeps showing the details it already had.
- New tests: tests/test_customer_api.py (29 tests pass in total).
