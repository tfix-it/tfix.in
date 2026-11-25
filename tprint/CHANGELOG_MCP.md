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

# Online orders (print.tfix.in)
- app/cloud_sync.py: fetches orders placed on print.tfix.in (even while the shop
  was closed), converts/prices each file like an in-shop upload, adds it as an
  unpaid queued job, acknowledges the website, and pushes job status back so
  the customer can follow the order. Idempotent; network errors retry next cycle.
- app/config.py: PRINTSHOP_CLOUD_URL / PRINTSHOP_CLOUD_TOKEN / ..._SYNC_INTERVAL_SEC.
- app/main.py: starts/stops the sync loop (off unless configured).
- tests/test_cloud_sync.py: end-to-end with the real PHP inbox (php -S + SQLite).

# CLUTCHPRINT accounts and #CP tokens
- Website: online customers sign in with mobile number (username) + 4-digit PIN; the first order
  sets the PIN; signed-in customers see all their #CP tokens; lockouts; PIN reset by staff.
- app/cloud_sync.py: CloudClient.reset_pin(); app/routes_admin.py: POST /api/admin/cloud/reset-pin
  (admin session + login rate limit).
- static/admin/index.html: ONLINE #CP label, search by token/customer name, Online customers panel
  (Reset PIN). Security: file names and customer names are now HTML-escaped in the job table —
  online orders come from the internet, and an unescaped name could have run script in the admin page.
- tests: account, lockout, session, PIN reset, change PIN, escaping (45 tests).

# CLUTCH Rewards replace PINs
- No PIN or sign-in. The mobile number (mandatory) is the customer's CLUTCH Rewards ID.
- Rewards: ₹5 for every ₹100 of online prints (cumulative), credited once per order when it is
  collected; the website's status shows the balance and what each order added.
- app/cloud_sync.py: status push now includes each job's final cost (staff price corrections
  count); CloudClient.rewards() / redeem() replace reset_pin().
- app/routes_admin.py: GET /api/admin/cloud/rewards, POST /api/admin/cloud/redeem (admin only);
  the reset-pin endpoint is gone.
- static/admin/index.html: CLUTCH Rewards panel (check balance + history, use rewards).

# Rewards log + per-file options
- Website: tamper-evident rewards log (hash chain, `reward_ledger`); shop.php `ledger_verify`; rewards
  lookups return the history and the log check. Customers see verified receipts on their pass.
- Admin CLUTCH Rewards panel: full history with receipt codes and a "Rewards log verified" line.
- Website: per-file ink / sides / copies / pages (T Print already took options per file).

# repair.tfix.in: Phone Doctor + repair booking
- New site `repair/`: browser Phone Doctor, booking (#RP tokens), repair pass; shares the database
  and CLUTCH Rewards with print.tfix.in (lib.php schema v5: repairs, repair_credits).
- shop.php: repairs, repair_update (collected + paid credits rewards once, logged in the hash chain).
- app/cloud_sync.py: CloudClient.repairs()/repair_update(); app/routes_admin.py: GET/POST
  /api/admin/cloud/repairs; admin page: Repairs panel (all customer text HTML-escaped).
- Tests: 50 pass.
