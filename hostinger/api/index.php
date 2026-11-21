<?php
/**
 * Single entry point for /api/* and /mcp (see ../.htaccess).
 *
 * Customer API, admin API (same paths and shapes as the shop's LAN admin, so
 * the admin page barely changed), print-agent API and the Claude connector.
 */
declare(strict_types=1);

require __DIR__ . '/../lib/bootstrap.php';

function route(string $method, string $path): void
{
    $m = [];
    $is = function (string $verb, string $pattern) use ($method, $path, &$m): bool {
        return $method === $verb && preg_match('#^' . $pattern . '$#', $path, $m) === 1;
    };

    // ---------------------------------------------------------- Claude connector
    if ($path === '/mcp' || $path === '/mcp/') {
        require __DIR__ . '/../lib/mcp.php';
        mcp_handle();
    }

    // ---------------------------------------------------------- customer
    if ($is('POST', '/api/upload')) {
        require_same_origin();
        customer_rate_limit();
        json_out(create_upload($_FILES['file'] ?? []));
    }
    if ($is('GET', '/api/uploads/([a-f0-9]{32})')) {
        json_out(upload_view(get_upload($m[1])));
    }
    if ($is('GET', '/api/uploads/([a-f0-9]{32})/pdf')) {
        $u = get_upload($m[1]);
        if ($u['status'] !== 'ready' || !$u['pdf_file']) {
            throw new HttpError(404, 'Not ready');
        }
        send_file(storage_path($u['pdf_file']), 'application/pdf', 'preview.pdf', true);
    }
    if ($is('POST', '/api/quote')) {
        json_out(quote_cost(form_int('pages'), form_int('pages_per_sheet', 1), form_bool('color'), form_bool('doubleside'),
            form_int('copies', 1), form_str('paper_size', 'A4'), preg_replace('/\D/', '', form_str('phone', '')),
            max(0, form_int('files_ahead_today', 0))));
    }
    if ($is('POST', '/api/jobs')) {
        require_same_origin();
        customer_rate_limit();
        $job = create_job([
            'phone' => preg_replace('/\D/', '', form_str('phone')),
            'upload_id' => form_str('pdf_filename'),
            'pages_range' => form_str('pages_range', ''),
            'color' => form_bool('color'),
            'doubleside' => form_bool('doubleside'),
            'duplex_edge' => form_str('duplex_edge', 'long'),
            'pages_per_sheet' => form_int('pages_per_sheet', 1),
            'copies' => form_int('copies', 1),
            'paper_size' => form_str('paper_size', 'A4'),
            'orientation' => form_str('orientation', 'portrait'),
            'scale_mode' => form_str('scale_mode', 'fit'),
        ]);
        json_out(customer_job_view($job));
    }
    if ($is('GET', '/api/jobs/status')) {
        $tokens = array_slice(array_filter(explode(',', (string) ($_GET['tokens'] ?? ''))), 0, 20);
        $out = [];
        foreach ($tokens as $t) {
            if (preg_match('/^[a-f0-9]{32}$/', $t) && ($j = row('SELECT * FROM jobs WHERE token=?', [$t]))) {
                $out[] = customer_job_view($j);
            }
        }
        json_out($out);
    }
    if ($is('GET', '/api/payment-info')) {
        $s = app_settings();
        json_out(['upi_id' => $s['upi_id'], 'upi_payee_name' => $s['upi_payee_name']]);
    }

    // ---------------------------------------------------------- admin: auth
    if ($is('GET', '/api/admin/setup-status')) {
        json_out(['setup_required' => !admin_password_is_set()]);
    }
    if ($is('POST', '/api/admin/setup')) {
        require_same_origin();
        login_rate_limit();
        $code = tprint_config()['setup_code'];
        if (admin_password_is_set()) {
            throw new HttpError(409, 'The admin password is already set. Sign in instead.');
        }
        if (strlen($code) < 16 || !hash_equals($code, form_str('setup_code'))) {
            throw new HttpError(401, 'Setup code is incorrect');
        }
        set_admin_password(form_str('new_password'));
        create_session();
        json_out(['ok' => true]);
    }
    if ($is('POST', '/api/admin/login')) {
        require_same_origin();
        login_rate_limit();
        if (!admin_password_is_set()) {
            throw new HttpError(409, 'setup_required');
        }
        $userOk = hash_equals(tprint_config()['admin_username'], form_str('username'));
        $passOk = verify_admin_password(form_str('password'));
        if (!($userOk && $passOk)) {
            throw new HttpError(401, 'Invalid credentials');
        }
        create_session();
        json_out(['status' => 'success']);
    }
    if ($is('POST', '/api/admin/logout')) {
        require_same_origin();
        if (!empty($_COOKIE[SESSION_COOKIE])) {
            q('DELETE FROM admin_sessions WHERE token_hash=?', [hash('sha256', $_COOKIE[SESSION_COOKIE])]);
        }
        set_session_cookie('', 0);
        json_out(['ok' => true]);
    }

    if (str_starts_with($path, '/api/admin/')) {
        if ($method !== 'GET') {
            require_same_origin();
        }
        require_admin();
        admin_routes($method, $path, $is, $m);
    }

    // ---------------------------------------------------------- print agent
    if (str_starts_with($path, '/api/agent/')) {
        require_agent();
        agent_routes($path, $is, $m);
    }

    throw new HttpError(404, 'Not found');
}

function admin_routes(string $method, string $path, callable $is, array &$m): void
{
    if ($is('POST', '/api/admin/change-password')) {
        login_rate_limit();
        if (!verify_admin_password(form_str('current_password'))) {
            throw new HttpError(401, 'Current password is incorrect');
        }
        set_admin_password(form_str('new_password'));
        q('DELETE FROM admin_sessions');          // sign every device out
        set_session_cookie('', 0);
        json_out(['ok' => true]);
    }
    if ($is('GET', '/api/admin/jobs')) {
        maybe_run_cleanup();
        $status = $_GET['status'] ?? '';
        $jobs = $status !== ''
            ? rows('SELECT * FROM jobs WHERE status=? ORDER BY created_at DESC LIMIT 500', [$status])
            : rows('SELECT * FROM jobs ORDER BY created_at DESC LIMIT 500');
        json_out(array_map('admin_job_view', $jobs));
    }
    if ($is('POST', '/api/admin/jobs/(\d+)/status')) {
        json_out(admin_job_view(update_job_status((int) $m[1], form_str('status'))));
    }
    if ($is('POST', '/api/admin/jobs/(\d+)/paid')) {
        $paid = form_bool('paid', true);
        $job = confirm_payment((int) $m[1], $paid);
        if ($paid) {
            $job = maybe_auto_print($job);
        }
        json_out(admin_job_view($job));
    }
    if ($is('POST', '/api/admin/jobs/(\d+)/print')) {
        json_out(admin_job_view(dispatch_job((int) $m[1], form_str('printer_name'))));
    }
    if ($is('POST', '/api/admin/jobs/(\d+)/cancel-print')) {
        json_out(admin_job_view(cancel_pending_dispatch((int) $m[1])));
    }
    if ($is('GET', '/api/admin/jobs/(\d+)/file')) {
        $job = must_get_job((int) $m[1]);
        $kind = $_GET['kind'] ?? 'pdf';
        $file = $kind === 'source' ? $job['source_file'] : $job['pdf_file'];
        if (!$file || (int) $job['files_deleted'] === 1) {
            throw new HttpError(404, 'File not found');
        }
        $isPdf = str_ends_with(strtolower($file), '.pdf');
        $name = $kind === 'source' ? $job['original_filename'] : pathinfo($job['original_filename'], PATHINFO_FILENAME) . '.pdf';
        send_file(storage_path($file), $isPdf ? 'application/pdf' : 'application/octet-stream', $name, $isPdf);
    }
    if ($is('GET', '/api/admin/printers')) {
        json_out(array_map(fn($p) => [
            'id' => (int) $p['id'], 'name' => $p['name'], 'windows_printer_name' => $p['windows_printer_name'],
            'enabled' => (int) $p['enabled'], 'live_status' => agent_status()['online'] ? $p['live_status'] : 'offline',
        ], rows('SELECT * FROM printers ORDER BY name')));
    }
    if ($is('POST', '/api/admin/printers/(\d+)/enabled')) {
        q('UPDATE printers SET enabled=? WHERE id=?', [(int) form_bool('enabled'), (int) $m[1]]);
        json_out(['ok' => true]);
    }
    if ($is('GET', '/api/admin/pricing')) {
        json_out(pricing_view());
    }
    if ($is('POST', '/api/admin/pricing')) {
        $size = form_str('paper_size');
        if (!in_array($size, ['A4', 'A3'], true)) {
            throw new HttpError(400, 'Unknown paper size');
        }
        $values = [form_float('singleside_cost'), form_float('doubleside_cost'), form_float('min_cost')];
        foreach ($values as $v) {
            if ($v < 0 || $v > 100000) {
                throw new HttpError(400, 'Prices must be between 0 and 100000');
            }
        }
        q('UPDATE pricing SET singleside_cost=?, doubleside_cost=?, min_cost=? WHERE paper_size=? AND color=?',
            [...$values, $size, (int) form_bool('color')]);
        json_out(['ok' => true]);
    }
    if ($is('GET', '/api/admin/auto-print')) {
        json_out(auto_print_view());
    }
    if ($is('POST', '/api/admin/auto-print')) {
        $printer = form_str('printer_name', '');
        q('UPDATE auto_print_settings SET enabled=?, printer_name=?, bw_only=?, max_sheets=?, max_copies=? WHERE id=1', [
            (int) form_bool('enabled'), $printer !== '' ? $printer : null, (int) form_bool('bw_only', true),
            form_optional_int('max_sheets'), form_optional_int('max_copies'),
        ]);
        json_out(auto_print_view());
    }
    if ($is('GET', '/api/admin/settings')) {
        json_out(public_settings_view(app_settings()));
    }
    if ($is('POST', '/api/admin/settings')) {
        $s = app_settings();
        $new = [
            'max_upload_mb' => form_int('max_upload_mb', $s['max_upload_mb']),
            'unpaid_cleanup_days' => form_int('unpaid_cleanup_days', $s['unpaid_cleanup_days']),
            'rate_limit_window_sec' => form_int('rate_limit_window_sec', $s['rate_limit_window_sec']),
            'rate_limit_max_requests' => form_int('rate_limit_max_requests', $s['rate_limit_max_requests']),
            'upi_id' => form_str('upi_id', $s['upi_id']),
            'upi_payee_name' => mb_substr(form_str('upi_payee_name', $s['upi_payee_name']), 0, 60),
        ];
        if ($new['max_upload_mb'] < 1) throw new HttpError(400, 'Max upload size must be at least 1 MB');
        if ($new['unpaid_cleanup_days'] < 1) throw new HttpError(400, 'Cleanup period must be at least 1 day');
        if ($new['rate_limit_window_sec'] < 1) throw new HttpError(400, 'Rate limit window must be at least 1 second');
        if ($new['rate_limit_max_requests'] < 0) throw new HttpError(400, "Rate limit max requests can't be negative");
        if ($new['upi_id'] !== '' && !preg_match('/^[A-Za-z0-9._-]{2,256}@[A-Za-z][A-Za-z0-9.-]{1,63}$/', $new['upi_id'])) {
            throw new HttpError(400, 'UPI ID should look like shopname@okaxis');
        }
        q('UPDATE app_settings SET max_upload_mb=?, unpaid_cleanup_days=?, rate_limit_window_sec=?, rate_limit_max_requests=?,
             upi_id=?, upi_payee_name=? WHERE id=1', array_values($new));
        json_out(public_settings_view(app_settings()));
    }
    if ($is('POST', '/api/admin/settings/run-cleanup-now')) {
        json_out(['ok' => true, 'removed' => run_unpaid_cleanup()]);
    }
    if ($is('GET', '/api/admin/agent')) {
        json_out(agent_status());
    }
    throw new HttpError(404, 'Not found');
}

function pricing_view(): array
{
    return array_map(fn($p) => [
        'id' => (int) $p['id'], 'paper_size' => $p['paper_size'], 'color' => (int) $p['color'],
        'singleside_cost' => (float) $p['singleside_cost'], 'doubleside_cost' => (float) $p['doubleside_cost'],
        'min_cost' => (float) $p['min_cost'],
    ], rows('SELECT * FROM pricing ORDER BY paper_size, color'));
}

function auto_print_view(): array
{
    $s = row('SELECT * FROM auto_print_settings WHERE id=1');
    return [
        'enabled' => (int) $s['enabled'], 'printer_name' => $s['printer_name'], 'bw_only' => (int) $s['bw_only'],
        'max_sheets' => $s['max_sheets'] === null ? null : (int) $s['max_sheets'],
        'max_copies' => $s['max_copies'] === null ? null : (int) $s['max_copies'],
    ];
}

function agent_routes(string $path, callable $is, array &$m): void
{
    if ($is('POST', '/api/agent/poll')) {
        $body = json_decode((string) file_get_contents('php://input'), true) ?: [];
        $now = now_utc();
        foreach (array_slice($body['printers'] ?? [], 0, 50) as $p) {
            $name = mb_substr(trim((string) ($p['name'] ?? '')), 0, 255);
            $status = in_array($p['status'] ?? '', ['ready', 'offline', 'error', 'paper_out', 'busy', 'unknown'], true) ? $p['status'] : 'unknown';
            if ($name !== '') {
                q('INSERT INTO printers (name, windows_printer_name, live_status, last_reported_at) VALUES (?,?,?,?)
                   ON DUPLICATE KEY UPDATE live_status=VALUES(live_status), last_reported_at=VALUES(last_reported_at)',
                    [$name, $name, $status, $now]);
            }
        }
        q('UPDATE app_settings SET agent_last_seen=?, agent_version=? WHERE id=1',
            [$now, mb_substr((string) ($body['agent_version'] ?? ''), 0, 40)]);
        maybe_run_cleanup();
        $dispatches = [];
        foreach (rows("SELECT d.job_id, d.printer, j.* FROM print_dispatches d JOIN jobs j ON j.id=d.job_id
                       WHERE d.state='pending' ORDER BY d.created_at LIMIT 10") as $r) {
            $dispatches[] = [
                'job_id' => (int) $r['job_id'], 'printer' => $r['printer'], 'copies' => (int) $r['copies'],
                'doubleside' => (bool) $r['doubleside'], 'duplex_edge' => $r['duplex_edge'], 'orientation' => $r['orientation'],
                'pages_range' => $r['pages_range'], 'scale_mode' => $r['scale_mode'], 'color' => (bool) $r['color'],
                'paper_size' => $r['paper_size'],
            ];
        }
        $conversions = array_map(fn($u) => ['upload_id' => $u['id'], 'file_type' => $u['file_type']],
            rows("SELECT id, file_type FROM uploads WHERE status='converting' ORDER BY created_at LIMIT 5"));
        json_out(['dispatches' => $dispatches, 'conversions' => $conversions, 'server_time' => iso($now)]);
    }
    if ($is('POST', '/api/agent/dispatches/(\d+)/claim')) {
        $n = q("UPDATE print_dispatches SET state='agent_claimed', updated_at=? WHERE job_id=? AND state='pending'",
            [now_utc(), (int) $m[1]])->rowCount();
        if ($n !== 1) {
            throw new HttpError(409, 'Not claimable (already claimed or cancelled)');
        }
        json_out(['ok' => true]);
    }
    if ($is('GET', '/api/agent/jobs/(\d+)/file')) {
        $d = row("SELECT * FROM print_dispatches WHERE job_id=? AND state='agent_claimed'", [(int) $m[1]])
            ?? throw new HttpError(409, 'Claim the dispatch first');
        $job = must_get_job((int) $m[1]);
        send_file(storage_path($job['pdf_file']), 'application/pdf', "job-{$job['id']}.pdf", false);
    }
    if ($is('POST', '/api/agent/dispatches/(\d+)/result')) {
        $body = json_decode((string) file_get_contents('php://input'), true) ?: [];
        $ok = ($body['ok'] ?? false) === true;
        if (!$ok && ($body['not_sent'] ?? false) === true) {
            // The agent failed BEFORE handing anything to Windows (e.g. download error):
            // nothing printed, so the job safely goes back to the queue.
            $jobId = (int) $m[1];
            $n = in_transaction(function () use ($jobId) {
                $n = q("DELETE FROM print_dispatches WHERE job_id=? AND state='agent_claimed'", [$jobId])->rowCount();
                if ($n === 1) {
                    q("UPDATE jobs SET status='queued', auto_printed=0, updated_at=? WHERE id=?", [now_utc(), $jobId]);
                }
                return $n;
            });
            json_out(['ok' => $n === 1]);
        }
        $n = q("UPDATE print_dispatches SET state=?, error=?, updated_at=? WHERE job_id=? AND state='agent_claimed'", [
            $ok ? 'submitted' : 'uncertain', $ok ? null : mb_substr((string) ($body['error'] ?? 'Print outcome uncertain'), 0, 250),
            now_utc(), (int) $m[1],
        ])->rowCount();
        q('UPDATE jobs SET updated_at=? WHERE id=?', [now_utc(), (int) $m[1]]);
        json_out(['ok' => $n === 1]);
    }
    if ($is('GET', '/api/agent/conversions/([a-f0-9]{32})/source')) {
        $u = get_upload($m[1]);
        if ($u['status'] !== 'converting') {
            throw new HttpError(409, 'Not waiting for conversion');
        }
        send_file(storage_path($u['source_file']), 'application/octet-stream', "{$u['id']}.{$u['file_type']}", false);
    }
    if ($is('POST', '/api/agent/conversions/([a-f0-9]{32})/result')) {
        $u = get_upload($m[1]);
        if ($u['status'] !== 'converting') {
            throw new HttpError(409, 'Not waiting for conversion');
        }
        $error = trim((string) ($_POST['error'] ?? ''));
        if ($error === '') {
            $f = $_FILES['pdf'] ?? null;
            $pdf = "{$u['id']}.pdf";
            if (!$f || $f['error'] !== UPLOAD_ERR_OK || !(move_uploaded_file($f['tmp_name'], storage_path($pdf)) || rename($f['tmp_name'], storage_path($pdf)))) {
                throw new HttpError(400, 'Missing converted PDF');
            }
            try {
                $pages = pdf_page_count(storage_path($pdf));      // counted here, not trusted from the agent
            } catch (HttpError $e) {
                @unlink(storage_path($pdf));
                $error = 'The converted file could not be read.';
            }
        }
        if ($error !== '') {
            q("UPDATE uploads SET status='failed', error=?, updated_at=? WHERE id=?", [mb_substr($error, 0, 250), now_utc(), $u['id']]);
        } else {
            q("UPDATE uploads SET status='ready', pdf_file=?, pages=?, updated_at=? WHERE id=?", [$pdf, $pages, now_utc(), $u['id']]);
        }
        json_out(['ok' => $error === '']);
    }
    throw new HttpError(404, 'Not found');
}

// ------------------------------------------------------------------ run
try {
    send_security_headers();
    route(request_method(), request_path());
} catch (HttpError $e) {
    json_out(['detail' => $e->getMessage()], $e->status);
} catch (PrintBlocked $e) {
    json_out(['detail' => $e->getMessage()], 409);
} catch (Throwable $e) {
    error_log('[tprint] ' . $e);
    json_out(['detail' => 'Server error — please try again.'], 500);
}
