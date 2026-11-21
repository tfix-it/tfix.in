# TPrint + Claude MCP — installation guide

TPrint's connector is an **MCP App**: besides plain tools, it gives Claude an interactive
print-queue dashboard that appears right inside the chat (job queue, payment state, printer
status, pricing, and a guarded Print button). It is built on the official MCP Python SDK 2.x
(`mcp.server.mcpserver.MCPServer` plus the `mcp.server.apps` extension).

> **Upgrading from the first connector (SDK 1.26)?** Copy the new `app` folder over (it now
> contains `app/mcp_ui/dashboard.html`), copy the new `requirements-mcp.txt`, then run
> `.\venv-mcp\Scripts\python.exe -m pip install --upgrade -r requirements-mcp.txt`.
> Your token, tunnel and Claude connector settings stay the same.
>
> **New: full admin online at https://print.tfix.in.** See section 3. It is off until you set `TPRINT_REMOTE_ADMIN=1`, and needs Cloudflare Access set up first. Restart the shop server too after copying `app`, so its admin page gets live updates from the connector.

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

The connector listens only on this PC at http://127.0.0.1:8765/mcp. Keep this window open. (Section 3 publishes it, together with the online admin, at https://print.tfix.in.) A normal browser request without the token returns Unauthorized: that is expected.

## 3. Publish at https://print.tfix.in

The connector process (port 8765) serves two things at **print.tfix.in**:

| Address | What | Who gets in |
|---|---|---|
| `https://print.tfix.in/` | The **full shop admin**: the same page as the shop's LAN admin. Job queue with live updates, Mark paid, Print, status changes, printers, auto-print, pricing, system settings, password change, PDF/original file links. | Cloudflare Access email login, then the shop admin password |
| `https://print.tfix.in/mcp` | Claude's connector (section 4) | Claude, with the bearer token |

The customer upload site is **never** published. Customers keep using `https://printatshop.local` on the shop WiFi. Only port 8765 goes through the tunnel. Never publish the shop server's port 443/8000 and never add router port forwarding.

> **This puts payment confirmation on the internet.** Anyone who gets past both logins can mark jobs paid (which can trigger auto-print), change prices, and download customers' files. Do every step below, including Cloudflare Access, before turning it on.

### 3.1 Prerequisites

- `tfix.in` uses Cloudflare for DNS (free plan is fine).
- cloudflared is installed on the shop PC: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
- Use a strong shop admin password (12+ characters). Change it in the admin's **Account** panel on the LAN first.

### 3.2 Create the tunnel (once)

```powershell
cloudflared tunnel login
cloudflared tunnel create tprint
cloudflared tunnel route dns tprint print.tfix.in
```

`create` prints a tunnel ID and writes `C:\Users\<you>\.cloudflared\<TUNNEL-ID>.json`. Keep that file private.

### 3.3 Put Cloudflare Access in front (once)

In the Cloudflare dashboard open **Zero Trust > Access > Applications**:

1. **Add an application > Self-hosted**, name `TPrint admin`, domain `print.tfix.in`, no path. Add a policy: Action **Allow**, Include **Emails**: only the owner's and trusted staff's email addresses. The login method can be the default **One-time PIN** (a code sent by email). Save, then copy the application's **Application Audience (AUD) tag**.
2. **Add an application > Self-hosted**, name `TPrint connector`, domain `print.tfix.in`, path `mcp`. Add a policy: Action **Bypass**, Include **Everyone**. Claude cannot do the email login; this path is protected by the bearer token instead.

Your team name is the first part of `<team>.cloudflareaccess.com` (Zero Trust > Settings).

### 3.4 Configure and start the tunnel

Copy `deploy/cloudflared-config.yml` from this package to `C:\Users\<you>\.cloudflared\config.yml` and fill in the tunnel ID, credentials file path, team name and AUD tag. With `access: required: true`, cloudflared itself rejects any admin request without a valid Access login, even if the Access application were later changed by mistake.

```powershell
cloudflared tunnel run tprint
```

Keep that window open (or install it as a Windows service with `cloudflared service install` once everything works).

### 3.5 Start the connector with the online admin turned on

In the shop folder, in its own PowerShell window:

```powershell
$env:TPRINT_MCP_TOKEN = 'PASTE_YOUR_GENERATED_TOKEN'
$env:TPRINT_MCP_PUBLIC_HOST = 'print.tfix.in'
$env:TPRINT_REMOTE_ADMIN = '1'
$env:TPRINT_MCP_ALLOW_PRINT = '0'
.\venv-mcp\Scripts\python.exe -m app.mcp_server
```

It prints `Online admin enabled at https://print.tfix.in/`. Without `TPRINT_REMOTE_ADMIN=1` only `/mcp` is served and every other address answers Unauthorized.

### 3.6 Check it

1. Open `https://print.tfix.in` in a private browser window. You should get the Cloudflare email-code page first, then the shop admin login.
2. Sign in and confirm the job queue matches the LAN admin. Upload a test file on the shop WiFi: it should appear online within a few seconds.
3. Open `https://print.tfix.in/mcp` in a browser. You should see `{"error":"Unauthorized"}` and **no** Cloudflare login page (that is the bypass working).
4. Try `https://print.tfix.in/api/upload` and `https://print.tfix.in/docs`: both must be Not Found.

Both admin pages, LAN and online, now update each other live. Each process watches the shared database every few seconds, so changes made online, by Claude, or by auto-print also reach customers' status screens.

**Turn the online admin off:** stop the connector, then start it again without `TPRINT_REMOTE_ADMIN`. **Cut everything off at once:** stop the `cloudflared` window or service. **Sign everyone out:** change the admin password; this ends every session, LAN and online.

### 3.7 Testing without a domain (optional)

`cloudflared tunnel --url http://127.0.0.1:8765` gives a temporary `*.trycloudflare.com` address with no Access login. Use it only for the Claude connector test, with `TPRINT_REMOTE_ADMIN` **unset**, and set `TPRINT_MCP_PUBLIC_HOST` to that hostname. This package uses JSON responses rather than an SSE response stream.

## 4. Add to Claude

In Claude's web interface open Customize > Connectors > Add custom connector.

- Name: TPrint
- URL: https://print.tfix.in/mcp
- Authentication: use the fixed Request headers configuration, not OAuth login.
- Header name: Authorization
- Header value: Bearer YOUR_GENERATED_TOKEN

This server uses a fixed secret, not OAuth. Do not place the token in the URL. If your Claude interface does not offer request headers, this connector needs an OAuth adapter before it can be connected there; do not disable authentication as a workaround.

Try: "Open the TPrint dashboard." Claude calls `open_print_dashboard` and the dashboard appears in the chat. Phone numbers and uploaded filenames are never sent to Claude or the dashboard. Connector tools are open_print_dashboard, list_print_jobs, list_printers, get_pricing, get_auto_print_settings and print_paid_job. No payment-confirmation, deletion or pricing-edit tool is exposed.

### 4.1 Using the dashboard

- **Stats and tabs**: queued, awaiting payment, printing and ready-to-collect counts; switch between Queued / Printing / Ready / Collected / All.
- **Refresh**: the dashboard asks the connector for fresh data. It does not update live by itself.
- **Printers**: every registered Windows printer with its reported status (ready, busy, paper out, offline, error). Disabled printers are greyed out.
- **Pricing & auto-print**: the current rates and auto-print rule, read-only.
- **Full admin** (shown when print.tfix.in is set up): opens https://print.tfix.in to mark payments, change prices and settings. Claude itself still has no payment, pricing or settings tools.
- **Print…** appears only on a job that is queued, marked Paid by staff, still has its file, and only while remote printing is enabled (section 5). Pressing it opens a confirmation row: choose the printer, then **Confirm print**. Claude may also ask you to approve the tool call. Unpaid jobs show "Staff must confirm payment first" instead.
- After a print, Claude is told which job went to which printer, so you can keep talking about it.

If a Claude client can't show MCP Apps, the same tool returns a plain-text summary of the queue instead.

### 4.2 Alternative: Claude Desktop on the shop PC (no tunnel, no token)

If Claude Desktop runs on the same PC as the shop server, it can start the connector itself over stdio. There is no network listener, tunnel or token. In Claude Desktop open Settings > Developer > Edit Config and add (adjust `C:\PrintShop` to your shop folder):

```json
{
  "mcpServers": {
    "tprint": {
      "command": "C:\\PrintShop\\venv-mcp\\Scripts\\python.exe",
      "args": ["-m", "app.mcp_server", "--transport", "stdio"],
      "env": { "PYTHONPATH": "C:\\PrintShop", "TPRINT_MCP_ALLOW_PRINT": "0" }
    }
  }
}
```

Restart Claude Desktop. Change `TPRINT_MCP_ALLOW_PRINT` to `"1"` only after the read-only test, as in section 5. Anyone who can use Claude Desktop on that PC can then use the connector, so lock the PC when unattended. Dashboard display depends on your Claude Desktop version supporting MCP Apps for local servers; the tools work either way.

## 5. Enable printing after the read-only test

Stop ONLY the MCP process. In its PowerShell window:

```powershell
$env:TPRINT_MCP_ALLOW_PRINT = '1'
.\venv-mcp\Scripts\python.exe -m app.mcp_server
```

Ask Claude to print one specific paid sample order to the exact Windows printer name, or open the dashboard and use Print… > Confirm print. The tool requires confirm=true after explicit user confirmation; the dashboard sends it only after you press Confirm print. This is a tool parameter, not a separate cryptographic approval workflow. Keep printing disabled when unattended if you want staff-only dispatch.

The database checks that payment is marked confirmed, the job is queued, the printer is enabled, the file exists inside uploads and the job has never been claimed for dispatch. A persistent unique job claim prevents concurrent/repeated submissions across Android, web, auto-print and MCP, including after a restart.

A successful response means SumatraPDF returned successfully; it does NOT prove paper physically printed. Changes made through Claude or print.tfix.in reach the LAN admin's live queue within a few seconds. The Android app may still need a refresh.

## 6. Payment and error handling

There is NO bank/UPI gateway integration in this version. Only authenticated staff can mark payment, using the Paid action in the Android app, the LAN admin or the online admin at print.tfix.in. Check the actual UPI/bank payment before marking a job paid, wherever you are. Audit entries record staff_manual, timestamp and paid value; the existing system has a shared admin identity, so this is not individual employee attribution.

If dispatch fails/times out or the process dies during printing, the claim stays locked. Check the Windows print queue and physical output. Do not reset the job status to queued expecting an automatic retry: the durable claim still blocks it. There is deliberately no remote reprint/unlock tool. After staff establish that another print is necessary, create a new job and explicitly reconcile its payment record; record any reprint/refund separately. Original print history remains intact.

Existing jobs already marked printing/ready/collected cannot print through the new gate. Payment cannot be revoked after a dispatch claim. Corrections/refunds require staff reconciliation.

To disable Claude and online-admin access immediately: stop the MCP window and the tunnel. To rotate access: generate a new token, restart MCP with it, and update Claude's header. Environment values above last only for that PowerShell session. Re-enter them at the next start; don't save secrets in shared scripts.

## Validation and limits

Automated tests (`python -m pytest`) run with a temporary SQLite database and a mocked Windows printer. They cover unpaid rejection, concurrent/repeated dispatch, uncertain failures, disabled printers, invalid file paths, payment audit, auto-print after staff payment, MCP authentication, tool discovery, the dashboard's ui:// resource and data (including that phone numbers and filenames never leave the PC), and real MCP tool calls. The dashboard was also driven in Chromium through the official MCP Apps host bridge (@modelcontextprotocol/ext-apps 2.0.4) against a live connector: handshake, theming, auto-resize, refresh, the Print > Confirm flow, refusal of a repeat print, and phone-width layout. Physical Windows printing and a live Claude connection still require your PC and account.

The original conversion, pricing and print-layout code is preserved. No Android TLS changes, bank notifications, shop-hours scheduler, automatic background Windows service or OAuth server have been added. The connector uses official MCP Python SDK 2.3.0 in a separate environment. The dashboard is one self-contained HTML file with no external scripts, fonts or network requests.

Rollback: stop BOTH servers and the tunnel; restore the complete dated backup. Do not restore an old database after new live transactions without reconciling those transactions first.

References:
- https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp
- https://github.com/modelcontextprotocol/python-sdk (v2.3.0)
- https://modelcontextprotocol.io/extensions/apps/overview (MCP Apps)
- https://github.com/modelcontextprotocol/ext-apps
- https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/
- https://developers.cloudflare.com/cloudflare-one/applications/configure-apps/self-hosted-apps/
