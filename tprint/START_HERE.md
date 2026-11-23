# TPrint + Claude MCP — installation guide

This update adds a separate MCP listener and a shared printing gate. It is source code, not a deployed service. Your Android APK does not need rebuilding for these backend changes.

## 1. Back up and update the existing shop server

1. Stop the shop server and finish/reconcile any jobs already printing.
2. Copy the ENTIRE existing shop folder to a dated backup, including printshop.db, uploads, cert.pem and key.pem. Keep that backup private.
3. Copy the `app` folder from this package over the existing server's `app` folder. Keep your existing database, uploads, certificates, virtual environment and working configuration. If you customized app/config.py, keep your existing copy of that file.
4. Start the shop server using your usual command. Alternatively, from the shop folder run `venv\Scripts\python.exe -m app.main` (uses the configured HTTPS certificate). Startup adds two audit tables; existing jobs and prices remain.
5. Test one unpaid sample job. Pressing Print must now be rejected. Check the actual payment in your UPI/bank app before marking a real order Paid.

IMPORTANT: marking Paid can now trigger auto-print immediately if auto-print is enabled and its rules match. Turn auto-print off for your first test. Existing paid flags are retained and treated as staff confirmations; they are not retroactively verified bank transactions.

## 2. Install the connector in its own Python environment

Open PowerShell IN YOUR EXISTING UPDATED SHOP FOLDER (the folder containing app and printshop.db). Copy requirements-mcp.txt from this package there too. Python 3.11 or 3.12 is recommended.

```powershell
py -3.12 -m venv venv-mcp
.\venv-mcp\Scripts\python.exe -m pip install -r requirements-mcp.txt
```

If you have Python 3.11 instead, replace `-3.12` with `-3.11`. Do not install these requirements into the old web-server environment: the MCP SDK uses a newer Starlette version.

Generate a private connector token:

```powershell
.\venv-mcp\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
```

Save it in your password manager. In that PowerShell window set:

```powershell
$env:TPRINT_MCP_TOKEN = 'PASTE_YOUR_GENERATED_TOKEN'
$env:TPRINT_MCP_ALLOW_PRINT = '0'
.\venv-mcp\Scripts\python.exe -m app.mcp_server
```

The connector listens only on this PC at http://127.0.0.1:8765/mcp. Keep this window open. A normal browser request without the token returns Unauthorized: that is expected.

## 3. Give Claude an HTTPS address

Publish ONLY port 8765 through an HTTPS tunnel, never the shop server's port 443/8000. The connector listener has no customer upload pages or file-download routes. Do not add router port forwarding for the shop website.

For a temporary test, install cloudflared following the official instructions:
https://developers.cloudflare.com/tunnel/get-started/

In another PowerShell window run:

```powershell
cloudflared tunnel --url http://127.0.0.1:8765
```

It displays a temporary HTTPS URL. Keep that window open. Stop the MCP process with Ctrl+C, then set the exact hostname from that URL (no https:// and no slash), keeping the same token:

```powershell
$env:TPRINT_MCP_PUBLIC_HOST = 'YOUR-EXACT-HOST.trycloudflare.com'
.\venv-mcp\Scripts\python.exe -m app.mcp_server
```

Quick tunnels are for testing; their address changes on restart. For regular use, configure a named tunnel with a stable hostname and service URL http://127.0.0.1:8765, then set TPRINT_MCP_PUBLIC_HOST to that hostname. Keep host validation enabled. This package uses JSON responses rather than an SSE response stream.

## 4. Add to Claude

In Claude's web interface open Customize > Connectors > Add custom connector.

- Name: TPrint
- URL: https://YOUR-EXACT-HOST/mcp
- Authentication: use the fixed Request headers configuration, not OAuth login.
- Header name: Authorization
- Header value: Bearer YOUR_GENERATED_TOKEN

This server uses a fixed secret, not OAuth. Do not place the token in the URL. If your Claude interface does not offer request headers, this connector needs an OAuth adapter before it can be connected there; do not disable authentication as a workaround.

Try: "Use TPrint to show queued jobs and configured printers." Phone numbers and uploaded filenames are excluded from the jobs tool. Connector tools are list_print_jobs, list_printers, get_pricing, get_auto_print_settings and print_paid_job. No payment-confirmation, deletion or pricing-edit tool is exposed.

## 5. Enable printing after the read-only test

Stop ONLY the MCP process. In its PowerShell window:

```powershell
$env:TPRINT_MCP_ALLOW_PRINT = '1'
.\venv-mcp\Scripts\python.exe -m app.mcp_server
```

Ask Claude to print one specific paid sample order to the exact Windows printer name. The tool requires confirm=true after explicit user confirmation. This is a tool parameter, not a separate cryptographic approval workflow. Keep printing disabled when unattended if you want staff-only dispatch.

The database checks that payment is marked confirmed, the job is queued, the printer is enabled, the file exists inside uploads and the job has never been claimed for dispatch. A persistent unique job claim prevents concurrent/repeated submissions across Android, web, auto-print and MCP, including after a restart.

A successful response means SumatraPDF returned successfully; it does NOT prove paper physically printed. MCP changes may require refreshing the Android/web queue because the separate listener does not broadcast to the existing process's WebSocket clients.

## 6. Payment and error handling

There is NO bank/UPI gateway integration in this version. Only authenticated staff can mark payment using the existing Android/web Paid action. Audit entries record staff_manual, timestamp and paid value; the existing system has a shared admin identity, so this is not individual employee attribution.

If dispatch fails/times out or the process dies during printing, the claim stays locked. Check the Windows print queue and physical output. Do not reset the job status to queued expecting an automatic retry: the durable claim still blocks it. There is deliberately no remote reprint/unlock tool. After staff establish that another print is necessary, create a new job and explicitly reconcile its payment record; record any reprint/refund separately. Original print history remains intact.

Existing jobs already marked printing/ready/collected cannot print through the new gate. Payment cannot be revoked after a dispatch claim. Corrections/refunds require staff reconciliation.

To disable Claude access immediately: stop the MCP window and tunnel. To rotate access: generate a new token, restart MCP with it, and update Claude's header. Environment values above last only for that PowerShell session. Re-enter them at the next start; don't save secrets in shared scripts.

## Validation and limits

Automated tests run with a temporary SQLite database and a mocked Windows printer. They cover unpaid rejection, concurrent/repeated dispatch, uncertain failures, disabled printers, invalid file paths, payment audit, auto-print after staff payment, MCP authentication, initialization, tool discovery and a real MCP tool call. Physical Windows printing and a live Claude connection still require your PC and account.

The original conversion, pricing and print-layout code is preserved. No Android TLS changes, bank notifications, shop-hours scheduler, automatic background Windows service or OAuth server have been added. The connector uses official MCP Python SDK 1.26.0 in a separate environment.

Rollback: stop BOTH servers and the tunnel; restore the complete dated backup. Do not restore an old database after new live transactions without reconciling those transactions first.

References:
- https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp
- https://github.com/modelcontextprotocol/python-sdk/tree/v1.26.0
- https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/

## 7. Online orders from print.tfix.in (orders while the shop is closed)

Customers can upload files and choose print options on https://print.tfix.in at any
time. Orders wait on the website. When this server runs on the shop PC it fetches
them every minute, converts and prices them with your own price table, and adds
each file to the normal queue as an UNPAID job. Nothing prints until staff mark it
Paid (the same print gate as in-shop jobs). The customer sees the status on the
website: Waiting for shop → Price confirmed → Paid → Printing → Ready → Collected.

The shop PC only makes outgoing HTTPS requests to print.tfix.in. No router port
forwarding or tunnel is needed for this.

Run this on Shop PC 1 only (the PC that runs the T Print server and is connected
to the printers). Set the two variables below on that PC only: if two PCs fetched
online orders, an order could be imported twice. Other PCs and phones use PC 1's
admin page over the shop WiFi (https://printatshop.local/admin) as usual.

Setup (once): put the shop token you were given in these two environment variables
in the PowerShell window that starts the shop server, then start it as usual:

```powershell
$env:PRINTSHOP_CLOUD_URL = 'https://print.tfix.in/api/shop.php'
$env:PRINTSHOP_CLOUD_TOKEN = 'PASTE_THE_SHOP_TOKEN'
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The console shows `[cloud] syncing online orders ...`, and `[cloud] imported N online
job(s)` when orders arrive. Online jobs appear in /admin with the order code and the
customer's name in the file name, e.g. `[TP3XL96D · Anu] notes.pdf — Note: ...`.

- Keep the token private (it lets a program download customer files). To change it,
  generate a new one, put its SHA-256 in the website's `_private/config.php`, and
  update PRINTSHOP_CLOUD_TOKEN here.
- Word/PowerPoint files are converted on this PC by LibreOffice, as for in-shop uploads.
  A file that can't be opened is reported to the customer on the website.
- Files are deleted from the website as soon as the shop PC has them.
- Leave PRINTSHOP_CLOUD_URL unset to turn online orders off.
- The website shows whether this server is running: "AI Auto Print online" (green)
  while T Print syncs, "Print server offline" when it hasn't synced for 3 minutes,
  and during opening hours "Print server offline — staff, please start T Print"
  (red). If you see red at the shop, check Shop PC 1.

## 8. Start T Print automatically on Shop PC 1 (and restart it if it stops)

`windows\watchdog.ps1` keeps T Print running: it starts it when Windows signs in,
checks every minute that it answers, and starts it again if it closed or froze.
Nobody has to remember to start it.

Once, on Shop PC 1:

1. Finish the normal setup first (venv, certificates, a working manual start).
2. Copy `tprint.env.example` to `tprint.env` in the same folder and paste the shop
   token into it. (This replaces typing the `$env:` lines from section 7.)
3. Open PowerShell **as administrator**, signed in as the Windows user that has the
   printers, go to the T Print folder and run:

```powershell
powershell -ExecutionPolicy Bypass -File .\windows\install-autostart.ps1
```

That creates the scheduled task "T Print Server" and starts it now. Within about a
minute the website shows "AI Auto Print online".

- Staff can double-click `windows\start-now.bat` to start it straight away.
- Logs: `logs\watchdog.log` (starts/restarts) and `logs\tprint-YYYYMMDD.log`.
- To remove: `powershell -ExecutionPolicy Bypass -File .\windows\uninstall-autostart.ps1`
  (as administrator).
- Don't also start T Print by hand in a PowerShell window: the watchdog does it.

So the server also comes back after a power cut or a Windows update restart:

- Windows: Settings → Accounts → Sign-in options — let the shop user sign in
  automatically (or sign in each morning); the task starts at sign-in.
- BIOS/UEFI of the PC: set "Restore on AC power loss" / "AC Power Recovery" to
  **Power On**, so the PC switches itself back on after a power cut.
