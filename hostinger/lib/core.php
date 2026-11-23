<?php
/**
 * Business rules: uploads, jobs, the durable print gate, payments,
 * auto-print, admin sessions, rate limits and cleanup.
 *
 * The print gate keeps the Python version's guarantees, adapted to a print
 * agent on the shop PC:
 *   1. dispatch_job() checks payment / queued status / enabled printer / file,
 *      then claims the job with a row in print_dispatches (job_id is the
 *      primary key, so a job can be dispatched once, ever) and sets status
 *      'printing'. State: pending.
 *   2. The agent atomically claims a pending dispatch (pending -> agent_claimed)
 *      before printing, so two agents or a retry can never print it twice.
 *   3. The agent reports submitted or uncertain. Nothing is retried
 *      automatically; an uncertain job needs staff to check the printer.
 *   A dispatch that is still pending (the agent never took it) can be cancelled.
 */
declare(strict_types=1);

const JOB_STATUSES = ['queued', 'printing', 'ready', 'collected'];
const UPLOAD_TYPES = ['pdf', 'jpg', 'jpeg', 'png', 'docx', 'pptx', 'ppt'];
const OFFICE_TYPES = ['docx', 'pptx', 'ppt'];
const SESSION_COOKIE = 'tprint_admin';
const SESSION_TTL_SEC = 12 * 3600;

// ------------------------------------------------------------------ settings
function app_settings(): array
{
    $s = row('SELECT * FROM app_settings WHERE id=1');
    foreach (['max_upload_mb', 'unpaid_cleanup_days', 'rate_limit_window_sec', 'rate_limit_max_requests'] as $k) {
        $s[$k] = (int) $s[$k];
    }
    return $s;
}

function public_settings_view(array $s): array
{
    return [
        'max_upload_mb' => $s['max_upload_mb'],
        'unpaid_cleanup_days' => $s['unpaid_cleanup_days'],
        'rate_limit_window_sec' => $s['rate_limit_window_sec'],
        'rate_limit_max_requests' => $s['rate_limit_max_requests'],
        'upi_id' => $s['upi_id'],
        'upi_payee_name' => $s['upi_payee_name'],
    ];
}

function seconds_since(?string $dbTime): ?int
{
    return $dbTime === null ? null : time() - (int) (new DateTimeImmutable($dbTime, new DateTimeZone('UTC')))->format('U');
}

/** Every print agent (shop PC) and whether it has checked in recently. */
function agents_list(): array
{
    $limit = tprint_config()['agent_offline_after_sec'];
    return array_map(function ($a) use ($limit) {
        $age = seconds_since($a['last_seen']);
        $printers = array_column(rows('SELECT windows_printer_name FROM printers WHERE agent_name=? ORDER BY name', [$a['name']]), 'windows_printer_name');
        return ['name' => $a['name'], 'version' => $a['version'], 'last_seen' => iso($a['last_seen']),
                'seconds_ago' => $age, 'online' => $age !== null && $age <= $limit, 'printers' => $printers];
    }, rows('SELECT * FROM agents ORDER BY name'));
}

function agent_online(?string $name): bool
{
    if ($name === null) {
        return false;
    }
    $a = row('SELECT last_seen FROM agents WHERE name=?', [$name]);
    $age = $a ? seconds_since($a['last_seen']) : null;
    return $age !== null && $age <= tprint_config()['agent_offline_after_sec'];
}

/** Summary: online if at least one shop PC is connected. */
function agent_status(): array
{
    $agents = agents_list();
    $online = array_values(array_filter($agents, fn($a) => $a['online']));
    $latest = $agents ? max(array_map(fn($a) => $a['last_seen'], $agents)) : null;
    return [
        'online' => count($online) > 0,
        'online_count' => count($online),
        'last_seen' => $latest,
        'agents' => $agents,
    ];
}

/** A printer is usable only while the shop PC it is attached to is online. */
function printer_live_status(array $p): string
{
    return agent_online($p['agent_name'] ?? null) ? $p['live_status'] : 'offline';
}

// ------------------------------------------------------------------ rate limits
function rate_limit(string $bucket, int $max, int $windowSec, string $message): void
{
    if ($max <= 0) {
        return;
    }
    $ip = client_ip();
    $since = (new DateTimeImmutable("-$windowSec seconds", new DateTimeZone('UTC')))->format('Y-m-d H:i:s.u');
    $count = (int) q('SELECT COUNT(*) FROM rate_hits WHERE bucket=? AND ip=? AND created_at > ?', [$bucket, $ip, $since])->fetchColumn();
    if ($count >= $max) {
        throw new HttpError(429, $message);
    }
    q('INSERT INTO rate_hits (bucket, ip, created_at) VALUES (?,?,?)', [$bucket, $ip, now_utc()]);
    if (random_int(1, 50) === 1) {
        q('DELETE FROM rate_hits WHERE created_at < ?', [(new DateTimeImmutable('-1 day', new DateTimeZone('UTC')))->format('Y-m-d H:i:s')]);
    }
}

function customer_rate_limit(): void
{
    $s = app_settings();
    rate_limit('customer', $s['rate_limit_max_requests'], $s['rate_limit_window_sec'],
        'Too many uploads from this device — please wait a bit and try again.');
}

function login_rate_limit(): void
{
    // Fixed, like the Python version: not loosenable from the panel it protects.
    rate_limit('login', 5, 15 * 60, 'Too many login attempts — please wait 15 minutes and try again.');
}

// ------------------------------------------------------------------ admin auth
function admin_password_is_set(): bool
{
    return row('SELECT id FROM admin_credentials WHERE id=1') !== null;
}

function set_admin_password(string $password): void
{
    if (strlen($password) < 10) {
        throw new HttpError(400, 'Password must be at least 10 characters');
    }
    q('INSERT INTO admin_credentials (id, password_hash, updated_at) VALUES (1,?,?)
       ON DUPLICATE KEY UPDATE password_hash=VALUES(password_hash), updated_at=VALUES(updated_at)',
        [password_hash($password, PASSWORD_DEFAULT), now_utc()]);
}

function verify_admin_password(string $password): bool
{
    $r = row('SELECT password_hash FROM admin_credentials WHERE id=1');
    // Always run a real hash check so timing doesn't reveal whether a password is set.
    $hash = $r['password_hash'] ?? password_hash(random_token(), PASSWORD_DEFAULT);
    return password_verify($password, $hash) && $r !== null;
}

function create_session(): string
{
    $token = random_token(32);
    $now = new DateTimeImmutable('now', new DateTimeZone('UTC'));
    q('INSERT INTO admin_sessions (token_hash, created_at, expires_at) VALUES (?,?,?)', [
        hash('sha256', $token), $now->format('Y-m-d H:i:s.u'),
        $now->modify('+' . SESSION_TTL_SEC . ' seconds')->format('Y-m-d H:i:s.u'),
    ]);
    set_session_cookie($token, SESSION_TTL_SEC);
    return $token;
}

function set_session_cookie(string $value, int $ttl): void
{
    setcookie(SESSION_COOKIE, $value, [
        'expires' => $ttl > 0 ? time() + $ttl : time() - 3600,
        'path' => '/',
        'secure' => str_starts_with(tprint_config()['public_origin'], 'https://'),
        'httponly' => true,
        'samesite' => 'Lax',
    ]);
}

function require_admin(): void
{
    $token = $_COOKIE[SESSION_COOKIE] ?? '';
    if ($token === '' || !row('SELECT token_hash FROM admin_sessions WHERE token_hash=? AND expires_at > ?',
            [hash('sha256', $token), now_utc()])) {
        throw new HttpError(401, 'Not authenticated');
    }
    if (random_int(1, 100) === 1) {
        q('DELETE FROM admin_sessions WHERE expires_at < ?', [now_utc()]);
    }
}

function require_agent(): void
{
    $key = tprint_config()['agent_key'];
    if (strlen($key) < 32 || !hash_equals($key, bearer_token())) {
        throw new HttpError(401, 'Unauthorized');
    }
}

// ------------------------------------------------------------------ views
function job_types(array $j): array
{
    foreach (['id', 'pages', 'sheets', 'copies', 'color', 'doubleside', 'pages_per_sheet', 'auto_printed', 'daily_seq', 'files_deleted', 'paid'] as $k) {
        if (array_key_exists($k, $j)) {
            $j[$k] = (int) $j[$k];
        }
    }
    $j['cost'] = (float) $j['cost'];
    $j['created_at'] = iso($j['created_at']);
    $j['updated_at'] = iso($j['updated_at']);
    return $j;
}

/** What the shop staff see (admin API). */
function admin_job_view(array $j): array
{
    $j = job_types($j);
    $d = row('SELECT state, printer, error, updated_at FROM print_dispatches WHERE job_id=?', [$j['id']]);
    $j['dispatch'] = $d ? ['state' => $d['state'], 'printer' => $d['printer'], 'error' => $d['error'], 'updated_at' => iso($d['updated_at'])] : null;
    $j['pdf_filename'] = $j['pdf_file'];          // admin page builds file links from these
    $j['source_filename'] = $j['source_file'];
    unset($j['token'], $j['upload_id']);
    return $j;
}

/** What a customer sees for their own job: nothing that unlocks files or other jobs. */
function customer_job_view(array $j): array
{
    $j = job_types($j);
    $keep = ['id', 'token', 'phone', 'daily_seq', 'original_filename', 'status', 'paid', 'cost', 'sheets',
             'copies', 'color', 'paper_size', 'doubleside', 'created_at'];
    return array_intersect_key($j, array_flip($keep));
}

function get_job(int $id): ?array
{
    return row('SELECT * FROM jobs WHERE id=?', [$id]);
}

function must_get_job(int $id): array
{
    return get_job($id) ?? throw new HttpError(404, 'Job not found');
}

// ------------------------------------------------------------------ uploads
function create_upload(array $file): array
{
    $settings = app_settings();
    if (($file['error'] ?? UPLOAD_ERR_NO_FILE) !== UPLOAD_ERR_OK) {
        $tooBig = in_array($file['error'] ?? 0, [UPLOAD_ERR_INI_SIZE, UPLOAD_ERR_FORM_SIZE], true);
        throw new HttpError($tooBig ? 413 : 400, $tooBig
            ? "File too large — the limit is {$settings['max_upload_mb']} MB."
            : 'Upload failed — please try again.');
    }
    if ($file['size'] > $settings['max_upload_mb'] * 1024 * 1024) {
        throw new HttpError(413, "File too large — the limit is {$settings['max_upload_mb']} MB.");
    }
    $original = basename(str_replace('\\', '/', (string) $file['name']));
    $original = mb_substr(preg_replace('/[\x00-\x1F\x7F<>"\'`]/u', '', $original) ?? '', 0, 200) ?: 'file';
    $ext = strtolower(pathinfo($original, PATHINFO_EXTENSION));
    if (!in_array($ext, UPLOAD_TYPES, true)) {
        throw new HttpError(400, 'Unsupported file type: ' . ($ext !== '' ? ".$ext" : '(none)'));
    }
    $id = random_token(16);
    $source = "$id-src.$ext";
    if (!move_uploaded_file($file['tmp_name'], storage_path($source)) && !rename($file['tmp_name'], storage_path($source))) {
        throw new HttpError(500, 'Could not store the upload.');
    }
    $now = now_utc();
    $up = ['id' => $id, 'original_filename' => $original, 'file_type' => $ext, 'source_file' => $source,
           'pdf_file' => null, 'pages' => null, 'status' => 'ready', 'error' => null];
    try {
        if ($ext === 'pdf') {
            $up['pdf_file'] = $source;
            $up['source_file'] = null;
            $up['pages'] = pdf_page_count(storage_path($source));
        } elseif (in_array($ext, ['jpg', 'jpeg', 'png'], true)) {
            $up['pdf_file'] = "$id.pdf";
            image_to_pdf(storage_path($source), storage_path($up['pdf_file']));
            $up['pages'] = 1;
        } else {
            $up['status'] = 'converting';   // the shop PC's agent converts Word/PowerPoint with LibreOffice
        }
    } catch (HttpError $e) {
        @unlink(storage_path($source));
        throw $e;
    }
    q('INSERT INTO uploads (id, original_filename, file_type, source_file, pdf_file, pages, status, error, ip, created_at, updated_at)
       VALUES (?,?,?,?,?,?,?,?,?,?,?)', [$id, $original, $ext, $up['source_file'], $up['pdf_file'], $up['pages'],
        $up['status'], null, client_ip(), $now, $now]);
    return upload_view(row('SELECT * FROM uploads WHERE id=?', [$id]));
}

function upload_view(array $u): array
{
    $view = [
        'upload_id' => $u['id'],
        'pdf_filename' => $u['id'],          // the customer page passes this back when submitting
        'source_filename' => null,
        'original_filename' => $u['original_filename'],
        'file_type' => $u['file_type'],
        'pages' => $u['pages'] === null ? null : (int) $u['pages'],
        'status' => $u['status'],
        'error' => $u['error'],
    ];
    if ($u['status'] === 'converting') {
        $view['shop_pc_online'] = agent_status()['online'];
    }
    return $view;
}

function get_upload(string $id): array
{
    if (!preg_match('/^[a-f0-9]{32}$/', $id)) {
        throw new HttpError(404, 'Upload not found');
    }
    return row('SELECT * FROM uploads WHERE id=?', [$id]) ?? throw new HttpError(404, 'Upload expired — please re-upload your file');
}

// ------------------------------------------------------------------ jobs
function count_jobs_by_phone_today(string $phone): int
{
    return (int) q('SELECT COUNT(*) FROM jobs WHERE phone=? AND created_at >= ?', [$phone, shop_day_start_utc()])->fetchColumn();
}

function quote_cost(int $pages, int $pps, bool $color, bool $doubleside, int $copies, string $size, string $phone, int $filesAhead): array
{
    $apply = true;
    if ($phone !== '') {
        $apply = (count_jobs_by_phone_today($phone) + $filesAhead) === 0;
    }
    [$cost, $sheets] = print_cost($pages, $pps, $color, $doubleside, $copies, $size, $apply);
    return ['cost' => $cost, 'sheets' => $sheets, 'min_cost_applied' => $apply];
}

function create_job(array $in): array
{
    if (!preg_match('/^\d{10}$/', $in['phone'])) {
        throw new HttpError(400, 'Enter a 10-digit mobile number');
    }
    foreach (['orientation' => ['portrait', 'landscape'], 'duplex_edge' => ['long', 'short'], 'scale_mode' => ['fit', 'actual'],
              'paper_size' => ['A4', 'A3']] as $field => $allowed) {
        if (!in_array($in[$field], $allowed, true)) {
            throw new HttpError(400, "$field must be one of: " . implode(', ', $allowed));
        }
    }
    $upload = get_upload($in['upload_id']);
    if ($upload['status'] !== 'ready' || !$upload['pdf_file'] || !is_file(storage_path($upload['pdf_file']))) {
        throw new HttpError(400, 'Upload expired — please re-upload your file');
    }
    // Pages are counted on the server from the stored PDF, never taken from the browser.
    [$selected, $range] = parse_pages_range($in['pages_range'], (int) $upload['pages']);
    $pages = count($selected);

    return in_transaction(function () use ($in, $upload, $pages, $range) {
        // Serialise per phone so daily numbering and the minimum-charge waiver stay correct.
        q('SELECT GET_LOCK(?, 5)', ['tprint-phone-' . $in['phone']]);
        try {
            $dailySeq = count_jobs_by_phone_today($in['phone']) + 1;
            [$cost, $sheets] = print_cost($pages, $in['pages_per_sheet'], $in['color'], $in['doubleside'],
                $in['copies'], $in['paper_size'], $dailySeq === 1);
            $now = now_utc();
            $token = random_token(16);
            q('INSERT INTO jobs (token, phone, original_filename, upload_id, pdf_file, source_file, file_type, pages, pages_range,
                 sheets, copies, color, doubleside, duplex_edge, pages_per_sheet, paper_size, orientation, scale_mode,
                 daily_seq, cost, status, paid, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', [
                $token, $in['phone'], $upload['original_filename'], $upload['id'], $upload['pdf_file'], $upload['source_file'],
                $upload['file_type'], $pages, $range, $sheets, $in['copies'], (int) $in['color'], (int) $in['doubleside'],
                $in['duplex_edge'], $in['pages_per_sheet'], $in['paper_size'], $in['orientation'], $in['scale_mode'],
                $dailySeq, $cost, 'queued', 0, $now, $now,
            ]);
            return must_get_job((int) db()->lastInsertId());
        } finally {
            q('SELECT RELEASE_LOCK(?)', ['tprint-phone-' . $in['phone']]);
        }
    });
}

function update_job_status(int $id, string $status): array
{
    if (!in_array($status, JOB_STATUSES, true)) {
        throw new HttpError(400, 'Unknown status');
    }
    must_get_job($id);
    q('UPDATE jobs SET status=?, updated_at=? WHERE id=?', [$status, now_utc(), $id]);
    return must_get_job($id);
}

// ------------------------------------------------------------------ payments
function confirm_payment(int $jobId, bool $paid): array
{
    in_transaction(function () use ($jobId, $paid) {
        $job = row('SELECT * FROM jobs WHERE id=? FOR UPDATE', [$jobId]) ?? throw new PrintBlocked('Job not found');
        if (!$paid && row('SELECT job_id FROM print_dispatches WHERE job_id=?', [$jobId])) {
            throw new PrintBlocked('Cannot revoke payment after dispatch; reconcile manually.');
        }
        q('UPDATE jobs SET paid=?, updated_at=? WHERE id=?', [(int) $paid, now_utc(), $jobId]);
        q('INSERT INTO payment_audit (job_id, paid, actor, ip, created_at) VALUES (?,?,?,?,?)',
            [$jobId, (int) $paid, 'staff_manual', client_ip(), now_utc()]);
    });
    return must_get_job($jobId);
}

// ------------------------------------------------------------------ print gate
function dispatch_job(int $jobId, string $printerName, string $actor = 'staff', bool $auto = false): array
{
    $printerRow = row('SELECT * FROM printers WHERE windows_printer_name=?', [$printerName]);
    if ($printerRow && !agent_online($printerRow['agent_name'])) {
        throw new PrintBlocked('The shop PC for this printer is offline. Start the T-PRINT agent on '
            . ($printerRow['agent_name'] ?: 'that PC') . ', then try again.');
    }
    in_transaction(function () use ($jobId, $printerName, $actor, $auto) {
        $job = row('SELECT * FROM jobs WHERE id=? FOR UPDATE', [$jobId]) ?? throw new PrintBlocked('Job not found');
        if ((int) $job['paid'] !== 1) {
            throw new PrintBlocked('Payment must be confirmed by staff before printing.');
        }
        if ($job['status'] !== 'queued') {
            throw new PrintBlocked('Only queued jobs can print; check existing print history.');
        }
        if (row('SELECT job_id FROM print_dispatches WHERE job_id=?', [$jobId])) {
            throw new PrintBlocked('Already dispatched or outcome uncertain. Inspect the Windows queue; do not retry.');
        }
        if (!row('SELECT id FROM printers WHERE windows_printer_name=? AND enabled=1', [$printerName])) {
            throw new PrintBlocked('Printer is not registered and enabled.');
        }
        if ((int) $job['files_deleted'] === 1 || !is_file(storage_path($job['pdf_file']))) {
            throw new PrintBlocked('Print file unavailable.');
        }
        $now = now_utc();
        try {
            q('INSERT INTO print_dispatches (job_id, printer, actor, state, created_at, updated_at) VALUES (?,?,?,?,?,?)',
                [$jobId, $printerName, $actor, 'pending', $now, $now]);
        } catch (PDOException $e) {
            throw new PrintBlocked('Already dispatched or outcome uncertain. Inspect the Windows queue; do not retry.');
        }
        q("UPDATE jobs SET status='printing', auto_printed=?, updated_at=? WHERE id=?", [(int) $auto, $now, $jobId]);
    });
    return must_get_job($jobId);
}

/** Undo a dispatch the shop PC has not picked up yet. Safe: the agent's claim is atomic. */
function cancel_pending_dispatch(int $jobId): array
{
    in_transaction(function () use ($jobId) {
        $d = row('SELECT * FROM print_dispatches WHERE job_id=? FOR UPDATE', [$jobId]);
        if (!$d || $d['state'] !== 'pending') {
            throw new PrintBlocked('Only a print the shop PC has not started yet can be cancelled.');
        }
        q('DELETE FROM print_dispatches WHERE job_id=?', [$jobId]);
        q("UPDATE jobs SET status='queued', auto_printed=0, updated_at=? WHERE id=?", [now_utc(), $jobId]);
    });
    return must_get_job($jobId);
}

function maybe_auto_print(array $job): array
{
    if (!(int) $job['paid'] || $job['status'] !== 'queued') {
        return $job;
    }
    $s = row('SELECT * FROM auto_print_settings WHERE id=1');
    if (!$s || !(int) $s['enabled'] || !$s['printer_name']) {
        return $job;
    }
    if ((int) $s['bw_only'] && (int) $job['color']) {
        return $job;
    }
    if ($s['max_sheets'] !== null && (int) $job['sheets'] > (int) $s['max_sheets']) {
        return $job;
    }
    if ($s['max_copies'] !== null && (int) $job['copies'] > (int) $s['max_copies']) {
        return $job;
    }
    try {
        return dispatch_job((int) $job['id'], $s['printer_name'], 'auto', true);
    } catch (PrintBlocked) {
        return must_get_job((int) $job['id']);   // e.g. shop PC offline: stays queued for staff
    }
}

// ------------------------------------------------------------------ cleanup
function run_unpaid_cleanup(): int
{
    $days = app_settings()['unpaid_cleanup_days'];
    $cutoff = (new DateTimeImmutable("-$days days", new DateTimeZone('UTC')))->format('Y-m-d H:i:s.u');
    $stale = rows('SELECT * FROM jobs WHERE paid=0 AND files_deleted=0 AND created_at < ?', [$cutoff]);
    foreach ($stale as $job) {
        foreach ([$job['pdf_file'], $job['source_file']] as $f) {
            if ($f) {
                @unlink(storage_path($f));
            }
        }
        q('UPDATE jobs SET files_deleted=1, updated_at=? WHERE id=?', [now_utc(), $job['id']]);
    }
    // Uploads nobody turned into a job within a day.
    $dayAgo = (new DateTimeImmutable('-1 day', new DateTimeZone('UTC')))->format('Y-m-d H:i:s.u');
    foreach (rows('SELECT u.* FROM uploads u LEFT JOIN jobs j ON j.upload_id=u.id WHERE j.id IS NULL AND u.created_at < ?', [$dayAgo]) as $u) {
        foreach ([$u['pdf_file'], $u['source_file']] as $f) {
            if ($f) {
                @unlink(storage_path($f));
            }
        }
        q('DELETE FROM uploads WHERE id=?', [$u['id']]);
    }
    q('UPDATE app_settings SET last_cleanup_at=? WHERE id=1', [now_utc()]);
    return count($stale);
}

function maybe_run_cleanup(): void
{
    $last = app_settings()['last_cleanup_at'];
    if ($last === null || strtotime($last . ' UTC') < time() - 3600) {
        run_unpaid_cleanup();
    }
}
