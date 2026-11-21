# TPrint at https://print.tfix.in (Hostinger only)

TPrint runs on your Hostinger web hosting. Customers order from anywhere, pay by UPI QR code, and a small
**print agent** on the shop PC prints the jobs. No Cloudflare, tunnel, VPS or router changes.

```
Customer phone ──► https://print.tfix.in ◄── Shop owner (admin, any device)
                    Hostinger: PHP + MySQL       ◄── Claude (connector at /mcp)
                          ▲
                          │ HTTPS, every 3 s (the shop PC calls out; nothing calls in)
                    Shop PC: print agent ──► SumatraPDF ──► Windows printers
                                         └─► LibreOffice (Word/PowerPoint → PDF)
```

| Where | What |
|---|---|
| `hostinger/` → `public_html/TPRINT` | Customer site, admin at `/admin/`, API, Claude connector at `/mcp` |
| `tprint-config.php` → next to `public_html` | Private settings: database, setup code, agent key, Claude token |
| `tprint-storage/` → next to `public_html` | Uploaded files: never directly downloadable |
| `app/` + `agent/` → shop PC | The print agent (reuses the shop's `app/printing.py`) |

---

## 1. Hostinger (once)

### 1.1 Database
hPanel → **Databases → MySQL Databases** → create a database and user, for example `u507672922_tprint`.
Keep the database name, user and password for step 1.3.

### 1.2 Upload the site
1. On your computer, open the `hostinger` folder from this repository, select **everything inside it**
   (including the hidden `.htaccess` and `.user.ini`) and make a zip.
2. hPanel → **Files → File Manager** → open `domains/tfix.in/public_html/TPRINT` (the folder Hostinger
   created for print.tfix.in) → upload the zip → **Extract** → delete the zip.
   You should now see `index.html`, `admin/`, `api/`, `assets/`, `lib/`, `.htaccess` in `TPRINT`.

### 1.3 Private settings
1. In File Manager go **up** to `domains/tfix.in` (the folder that *contains* `public_html`).
2. Create `tprint-config.php` there with the contents of `hostinger/tprint-config.sample.php`, and fill in:
   - the database name, user and password from 1.1,
   - `setup_code`, `agent_key`, `mcp_token`: three **different** random 48-character values
     (a password manager's generator is fine).
3. Delete `tprint-config.sample.php` from `public_html/TPRINT` (it is blocked from the web anyway).

The file stays outside `public_html`, so no one can download it. Uploaded customer files go to
`domains/tfix.in/tprint-storage/`, also outside `public_html`; the site creates it on first upload.

### 1.4 PHP and HTTPS
- hPanel → **Advanced → PHP Configuration**: PHP **8.2 or newer** (8.3 recommended). Under PHP Extensions make
  sure `pdo_mysql`, `gd` and `zlib` are ticked (they normally are).
- hPanel → **Security → SSL**: make sure print.tfix.in has an SSL certificate, and turn on **Force HTTPS**.
- Upload size: `.user.ini` allows 64 MB per upload. The admin's "Max upload size" setting (default 50 MB) is the real limit.

### 1.5 First sign-in
1. Open **https://print.tfix.in/admin/**. It shows **First-time setup**.
2. Enter the `setup_code` from `tprint-config.php` and choose the admin password (10+ characters). This only works once.
3. In **System settings** enter your **UPI ID** (e.g. `tfixprint@okaxis`) and the payee name, then Save.
   Customers now get a UPI QR code for their exact amount after ordering.
4. Check **Pricing** and **Auto-print**.

The database tables are created automatically on the first visit.

---

## 2. Shop PC: print agent (once)

On the Windows PC connected to the printers:

1. Install **Python 3.12** (tick "Add python.exe to PATH"), **SumatraPDF** and **LibreOffice**,
   as in README.md section 1. Make sure the printers work normally in Windows.
2. Copy the `app` and `agent` folders from this repository to `C:\TPrint` (so you have `C:\TPrint\app` and `C:\TPrint\agent`).
   If SumatraPDF or LibreOffice are not in their default folders, set `SUMATRA_PATH` / `LIBREOFFICE_PATH` (see `app/config.py`).
3. In PowerShell:
   ```powershell
   cd C:\TPrint
   py -3.12 -m venv venv-agent
   .\venv-agent\Scripts\python.exe -m pip install -r agent\requirements.txt
   setx TPRINT_AGENT_KEY "PASTE_THE_agent_key_FROM_tprint-config.php"
   ```
   Close PowerShell (so `setx` takes effect), then double-click **`C:\TPrint\agent\start-agent.bat`**.
4. In the admin page, **Printers** should say **Shop PC connected** and list your Windows printers within a few seconds.
5. Start it automatically: Windows **Task Scheduler** → Create Basic Task → "When I log on" → Start a program →
   `C:\TPrint\agent\start-agent.bat`.

If the shop PC is off, customers can still order and pay. Jobs wait in the queue and print once the PC is back.
Word/PowerPoint uploads need the shop PC on (it converts them); customers are asked to upload a PDF otherwise.

---

## 3. Daily use

**Customer:** opens print.tfix.in (put a QR code to it on the counter), uploads PDF/JPG/PNG/Word/PowerPoint,
chooses options, enters their mobile number and sends. The next page shows their job code and a **UPI QR code**
for the exact total (note: `TPrint #<job number>`), plus a "Pay with a UPI app" button on phones. The page updates
by itself: once staff confirm the payment it shows "Payment received", then the print status.

**Shop owner** (admin, from any phone or computer):
1. Check the payment in your UPI app (amount and `TPrint #…` note), then press **Mark paid**.
2. Press **Print** (or let auto-print do it). The row shows *Waiting for the shop PC → Sending → Sent to <printer>*.
3. A print still *waiting for the shop PC* can be **cancelled**. Once sending has started it can't be.
4. **"Check printer … Not retried automatically"** means SumatraPDF failed or timed out. Look at the printer and
   the Windows print queue before doing anything; the job will not print twice by itself.
5. Set the status to Ready / Collected as usual.

Pages are counted on the server, not taken from the customer's browser, so prices can't be tampered with.

---

## 4. Claude connector (optional)

Claude can show the live queue as an interactive dashboard and, if you allow it, send paid jobs to a printer.

- In Claude: Customize → Connectors → Add custom connector
  - URL: `https://print.tfix.in/mcp`
  - Request header `Authorization` = `Bearer <mcp_token from tprint-config.php>`
- Try: "Open the TPrint dashboard."
- To let Claude print paid jobs, set `'mcp_allow_print' => true` in `tprint-config.php`.
- To turn the connector off, set `'mcp_token' => ''`.

Claude never sees customer phone numbers or file names, and cannot mark payments, change prices or delete anything.
The dashboard's **Full admin** button opens print.tfix.in/admin/ for that.

---

## 5. Security notes

- Public: the order page and its API, rate-limited per visitor. A customer can only see their own order
  (through a private link token); job numbers and phone numbers don't reveal anyone else's order.
- Customer files are stored outside `public_html` and served only to a signed-in admin, to the agent while it
  prints, or as the customer's own preview right after upload.
- Admin: password (hashed), sessions in HttpOnly/Secure/SameSite cookies, 5 login attempts per 15 minutes per
  visitor, and cross-site requests refused. Changing the password signs every device out.
- Agent and Claude use separate secret keys from `tprint-config.php`. Change a key there to revoke it at once.
- Unpaid jobs' files are deleted after the cleanup period (System settings). Uploads never turned into an order are deleted after a day.
- Backups: keep a copy of `tprint-config.php`, and download a database backup from hPanel (Files → Backups) from time to time.

**Updating:** upload a new zip of `hostinger/` over `public_html/TPRINT` (your config and files are outside it,
so they are kept). Database changes apply automatically. For the agent, copy the new `app` and `agent` folders and restart it.

---

## 6. Testing (developers)

`python -m pytest` runs the Python tests and, when PHP 8.1+ (with `pdo_mysql`, `gd`) and a MySQL/MariaDB test
database are available (see `tests/hostinger/conftest.py` for the `TPRINT_TEST_DB_*` settings), the PHP site,
the print agent and the Claude connector end to end under `php -S`.
