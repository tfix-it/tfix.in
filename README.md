# T FIX® CUSAT — CLUTCHPRINT AI + T Print (full source)

Two parts that work together:

| Folder | What it is | Runs on |
|---|---|---|
| `print/` | **CLUTCHPRINT website** — print.tfix.in. Customer order page (HTML/JS, Tailwind) + PHP API | Hostinger (PHP 8 + MySQL) |
| `repair/` | **T FIX® Repair Lab** — repair.tfix.in: Phone Doctor (browser self-check) + repair booking + repair pass. Its PHP uses `print/api/lib.php` (same database, same CLUTCH Rewards) | Hostinger (`public_html/repair/`) |
| `tprint/` | **T Print shop server** — prints, prices, admin (incl. Repairs panel), pulls online orders | Shop PC 1 (Windows, Python/FastAPI) |
| `tools/tailwind/` | Build step for the website CSS | Your computer (Node.js) |

## print/ — website (frontend + backend)

| File | Role |
|---|---|
| `index.html` | The whole customer page: layout, English + Malayalam text (`T` dictionary), prices (`SHOP`, `RATES`), upload, per-file options, pickup pass, rewards card. Edit text/prices near the top of the `<script>`. |
| `assets/app.css` | Compiled Tailwind CSS. **Do not edit by hand** — rebuild (below) after changing classes in `index.html`. |
| `api/lib.php` | Shared backend: database schema, limits, rewards maths (₹5 per ₹100), tamper-evident rewards log (`reward_ledger`). |
| `api/order.php` | Customer places an order (name, mobile, files, options per file). |
| `api/status.php` | Customer tracks an order (needs code + secret link) incl. rewards + history. |
| `api/server.php` | Public "print server online/offline". |
| `api/shop.php` | Shop-only API (needs the shop token): pending, file, ack, status, rewards, redeem, ledger_verify, cancel. |
| `_private/config.sample.php` | Copy to `_private/config.php` on the server and fill in DB + shop token hash. **Never commit config.php.** |
| `.htaccess`, `api/.htaccess`, `_private/.htaccess` | Cache rules, block lib.php and _private from the web. |
| `BRAND.md` | Brand brief and copy rules. |

### Rebuild the CSS after editing classes
```
cd tools/tailwind
npm install
npm run build        # both sites (or build:print / build:repair)
```

## repair/ — repair.tfix.in

| File | Role |
|---|---|
| `index.html` | Phone Doctor tests (display, touch, multi-touch, speaker, mic, cameras, charging, battery, vibration, motion), booking form, repair pass with rewards. English + Malayalam. |
| `api/book.php` | Customer books a repair (mobile number mandatory; Phone Doctor report attached if chosen). Token `#RP-<n>`. |
| `api/status.php` | Customer tracks a repair (needs the private link) incl. CLUTCH Rewards. |
| `.htaccess` | Security headers; camera/mic/motion allowed on this site only (for the Phone Doctor). |

Staff handle repairs in T Print admin → **Repairs** (via `shop.php?action=repairs / repair_update`).
Marking a repair Collected (final amount + paid) adds CLUTCH Rewards: ₹5 per ₹100, shared with print.

### Deploy the website
Upload the changed files into `public_html/print/` (Hostinger File Manager, keep the same folders),
then clear the cache in hPanel. `_private/config.php` on the server stays as it is.

## tprint/ — shop server (Shop PC 1)

| Path | Role |
|---|---|
| `app/main.py` | FastAPI app start-up. |
| `app/config.py` | Settings (prices, printers, paths). Your PC's own copy has your settings — keep it. |
| `app/pricing.py`, `app/print_service.py`, `app/printing.py` | Page count, price, the "paid before print" rule, sending to printers. |
| `app/cloud_sync.py` | Pulls online orders from print.tfix.in, pushes status/prices back; rewards lookup/redeem. |
| `app/routes_admin.py`, `app/routes_customer.py` | Admin and customer HTTP routes. |
| `static/admin/index.html` | Admin page (jobs, payments, CLUTCH Rewards panel). |
| `static/customer/` | In-shop customer upload page. |
| `windows/` | Auto-start watchdog (Task Scheduler). |
| `tprint.env.example` | Copy to `tprint.env` with the website URL + shop token. **Never commit tprint.env.** |
| `START_HERE.md` | Install / update / daily use guide for the shop PC. |

### Run the tests (website + shop server together)
```
cd tprint
pip install -r requirements.txt pytest httpx
python -m pytest tests          # the website tests also need `php` installed
```

## Secrets (never in this source)
- Website: `print/_private/config.php` (database password, shop token hash) — only on the server.
- Shop PC: `tprint/tprint.env` (shop token), `config.py`, `printshop.db`, `uploads/`, certificates.
