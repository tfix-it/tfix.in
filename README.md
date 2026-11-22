# Shop Print Server

> **T-PRINT (T FIX Auto Print) now runs at https://print.tfix.in on Hostinger** (PHP + MySQL), with UPI QR payment,
> a print agent on the shop PC and the Claude connector: see **[HOSTINGER.md](HOSTINGER.md)**.
> The Python server described below is the original shop-WiFi version; the print agent reuses its
> `app/printing.py`. START_HERE.md covers the older Cloudflare-tunnel setup.

A local-network web app for a print shop: customers upload files from their
phone/laptop over WiFi, configure how they want them printed, and get a
live price; the shop owner sees a live job queue, prints jobs to the actual
Windows printers, and marks them paid.

No internet connection required to run — everything happens on the shop's
own WiFi.

## 1. Install prerequisites (on the Windows PC that will run the server)

1. **Python 3.11+** — https://www.python.org/downloads/windows/
   (tick "Add python.exe to PATH" during install)

2. **LibreOffice** (used to convert uploaded .docx/.pptx/.ppt files to PDF)
   https://www.libreoffice.org/download/download/
   Default install path is already set in `app/config.py`
   (`C:\Program Files\LibreOffice\program\soffice.exe`) — edit if yours differs.

3. **SumatraPDF** (used to send PDFs to the printer silently, with duplex,
   copies, orientation, scale and page-range control)
   https://www.sumatrapdfreader.org/download-free-pdf-viewer
   Grab the portable `.exe` — no installer needed. Put it somewhere like
   `C:\Tools\SumatraPDF\SumatraPDF.exe` and update `SUMATRA_PATH` in
   `app/config.py` (or set the `SUMATRA_PATH` environment variable) to match.

4. Make sure your printers are already installed and working normally in
   Windows (visible in Settings → Printers & scanners) before wiring them
   into this app.

## 2. Install Python dependencies

Open PowerShell in the project folder:

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```
## 2.1 Create Certificate file
Run the following code in powershell after changing the ip address mentioned to that of the PC

python -c "
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import datetime, ipaddress

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, u'printatshop.local')])
cert = (x509.CertificateBuilder()
    .subject_name(name).issuer_name(name).public_key(key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(datetime.datetime.utcnow())
    .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=3650))
    .add_extension(x509.SubjectAlternativeName([
        x509.DNSName(u'printatshop.local'),
        x509.IPAddress(ipaddress.ip_address(u'10.179.37.40')),
    ]), critical=False)
    .sign(key, hashes.SHA256()))

open('cert.pem','wb').write(cert.public_bytes(serialization.Encoding.PEM))
open('key.pem','wb').write(key.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
    serialization.NoEncryption()))
print('cert.pem and key.pem written to', __import__('os').getcwd())
"

## 3. Configure

Open `app/config.py` and check/change:

- `SUMATRA_PATH`, `LIBREOFFICE_PATH` — match your actual install paths
- `ADMIN_USERNAME`, `ADMIN_PASSWORD` — change from the defaults
- `MDNS_HOSTNAME` — the `.local` name customers will use (default gives you `printatshop.local`)
- `DEFAULT_PRICING` — seed pricing (₹) used only the first time the database
  is created; after that, edit prices from the admin dashboard instead
- `DEFAULT_MAX_UPLOAD_MB`, `DEFAULT_UNPAID_CLEANUP_DAYS`,
  `DEFAULT_RATE_LIMIT_WINDOW_SEC`, `DEFAULT_RATE_LIMIT_MAX_REQUESTS` — seed
  values for the abuse-prevention settings below; also only used on first
  run, then editable from the admin dashboard's "System settings" panel
- `CLEANUP_INTERVAL_SEC` — how often the background sweep checks for
  expired unpaid-job files (not exposed in the UI; the retention period is)

You can also set any of these as environment variables instead of editing
the file directly (see the `os.environ.get(...)` calls in `config.py`).

## 4. Run the server

```powershell
venv\Scripts\activate
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Leave this window open — it's your running server. On first run it creates
`printshop.db` (SQLite) with default pricing and default system settings,
and starts advertising itself on the network.

## 5. Allow it through Windows Firewall

The first time you run it, Windows will likely prompt to allow network
access — click **Allow**. If it doesn't prompt, add a rule manually:

```powershell
netsh advfirewall firewall add rule name="PrintShop" dir=in action=allow protocol=TCP localport=8000
```

## 6. Connect from a phone

1. Connect the phone to the same shop WiFi.
2. Open a browser and go to: **`http://printatshop.local:8000`**
   (or `http://printatshop.local:8000/admin` for the owner's dashboard)
3. If `.local` resolution doesn't work on a particular device (rare on
   modern iOS/Android, more common on some older Android builds), fall back
   to the PC's LAN IP shown in the server's console log, e.g.
   `http://192.168.1.23:8000`.

A QR code pointing at that same URL, printed and placed at the counter, is
the easiest way to get customers there without typing anything.

## Project layout

```
app/
  config.py       — all paths, ports, admin credentials, default pricing/settings
  db.py           — SQLite: jobs, printers, pricing, auto-print, app settings
                    and admin session tables
  pricing.py      — cost calculation (refactored from pdfcostcalc.py)
  convert.py      — normalizes pdf/jpg/png/docx/pptx/ppt uploads into a PDF
  printing.py     — Windows printer discovery + SumatraPDF submission
  mdns_service.py — advertises printatshop.local on the LAN
  main.py         — FastAPI app: customer + admin APIs, WebSocket hub, cleanup loop
  mcp_server.py   — Claude connector: MCP App (in-chat dashboard) + tools, see START_HERE.md
  mcp_ui/dashboard.html — the dashboard UI served to Claude as ui://tprint/dashboard.html
  public_server.py — online admin at print.tfix.in (same admin page/API, guarded)
  job_watch.py    — live updates across the LAN server and the connector process
deploy/
  cloudflared-config.yml — tunnel + Cloudflare Access ingress for print.tfix.in
static/
  customer/index.html — mobile-first upload/quote/preview/status page
  admin/index.html     — login, live job queue, printer status, auto-print,
                          system settings and pricing editor
uploads/           — uploaded files land here (created automatically)
```

## What the customer sees

- Upload one or more files (PDF, JPG, PNG, DOCX, PPT/PPTX) — each is
  converted to PDF and priced independently, but can all be submitted
  together as one batch (one print job per file).
- Per-file print options: color/B&W, single/double-sided (with long- or
  short-edge flip), portrait/landscape, paper size (A4/A3), pages per
  sheet (n-up), copies, page range (all / odd / even / custom, e.g.
  `1-5, 8, 11-15`), and fit-to-page vs actual-size scaling.
- A live layout preview (via pdf.js) showing exactly how each physical
  sheet will look, front and back, with the chosen n-up layout and page
  selection.
- A live price quote as options change, including an automatic minimum-
  charge waiver from a phone number's 2nd job of the day onward.
- Multiple files can be queued up ("+ Add another file"), each with its own
  settings, reviewed together with a running grand total before sending.
- A confirmation sheet summarizing every file and the total before jobs are
  actually created.
- A live status screen (WebSocket, with polling as a fallback) tracking
  each job from queued → printing → ready → collected.

## What the shop owner sees (`/admin`)

- **Job queue** — searchable by phone number or job code, filterable by
  date, with live updates as jobs come in or change status. Each row shows
  file links (PDF and, if applicable, the original source file), print
  options, cost, status, paid/unpaid, and whether it was auto-printed.
- **Printers** — live status (ready/offline/error/paper out/busy) for every
  Windows printer detected on the PC.
- **Auto-print** — an optional rule that sends a new job straight to a
  chosen printer the moment it's submitted, if it matches conditions (B&W
  only, max sheets, max copies). Jobs that don't match stay queued for
  manual printing.
- **System settings** — admin-adjustable abuse-prevention knobs, applied
  immediately without a server restart:
  - Max upload size (MB)
  - Unpaid-job file retention (days) before files are auto-deleted from
    disk — the job row itself stays for record-keeping, with a "Run
    cleanup now" button for testing or right after lowering the period
  - Per-device (IP) rate limit — max requests per time window, shared
    across upload and job submission; set max requests to 0 to disable
- **Pricing** — per paper size / color editable single-side, double-side,
  and minimum-charge rates.

## What changed from your original `pdfcostcalc.py`

- Rates (`color_cost` / `bw_cost` dicts) now live in the `pricing` table
  instead of being hardcoded, so the admin dashboard can edit them.
- The minimum-charge floor is still applied to B&W jobs only, matching your
  original script's behavior — it's waived automatically from a phone
  number's 2nd job of the day onward (`apply_min_cost` in `pricing.py`).

## Known scaffold limitations (worth tightening before real deployment)

- Admin sessions are DB-backed (`admin_sessions` table, 12-hour expiry) so
  they survive a server restart, but the token itself is an unsigned opaque
  string looked up on every request — fine for a single-shop LAN, but for
  anything more exposed swap this for real hashed-password + signed-token
  handling, and add a background sweep for expired rows instead of the
  current lazy per-request prune if the table sees heavy traffic.
- The customer-facing `GET /api/jobs/by-phone/{phone}` endpoint has no
  auth by design (a customer looks up jobs tied to the number they typed
  in, same as asking at the counter) — only today's status/cost/filename
  are returned, but be aware it's an open lookup by phone number.
- Admin credentials (`ADMIN_USERNAME` / `ADMIN_PASSWORD`) default to values
  in `config.py` — make sure these are changed before going live.
