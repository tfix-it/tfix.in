<?php
/**
 * T Print online order inbox — shared helpers.
 *
 * Customers place orders on print.tfix.in at any time. Orders and files are
 * kept here until the T Print shop server (on the shop PC) fetches them
 * through shop.php. Files are stored OUTSIDE the web root and deleted from
 * this server once the shop has them.
 */
declare(strict_types=1);

if (realpath($_SERVER['SCRIPT_FILENAME'] ?? '') === __FILE__) {
    http_response_code(404);
    exit;
}

const ALLOWED_EXT = ['pdf', 'jpg', 'jpeg', 'png', 'docx', 'pptx', 'ppt'];
const MAX_FILES_PER_ORDER = 10;
const MAX_FILE_BYTES = 25 * 1024 * 1024;
const MAX_ORDER_BYTES = 60 * 1024 * 1024;
const ORDERS_PER_IP_PER_HOUR = 6;
const ALLOWED_PPS = [1, 2, 4, 6, 9, 16];
const ORDER_STATUSES = ['pending', 'received', 'paid', 'printing', 'ready', 'collected', 'cancelled'];
const JOB_STATUSES = ['queued', 'printing', 'ready', 'collected', 'failed'];
// CLUTCH Rewards: every ₹100 of collected (paid) online prints earns ₹5, per mobile number.
const REWARD_STEP = 100;
const REWARD_PER_STEP = 5;

const SERVICE_PAUSED = 'Online ordering is paused for a moment. Please try again in a few minutes, or call the shop.';

function cfg(): array
{
    static $c = null;
    if ($c === null) {
        $path = getenv('TPRINT_CLOUD_CONFIG') ?: dirname(__DIR__) . '/_private/config.php';
        if (!is_file($path)) {
            fail(503, 'Online ordering is not configured yet.');
        }
        try {
            $c = require $path;
        } catch (Throwable $e) {
            // Never log $e->getMessage(): for a typo on the password line PHP quotes the password.
            error_log('[print.tfix.in] _private/config.php cannot be read: ' . get_class($e) . ' on line ' . $e->getLine());
            $c = null;
        }
        if (!is_array($c) || empty($c['db_dsn'])) {
            $c = null;
            fail(503, SERVICE_PAUSED);
        }
    }
    return $c;
}

function data_dir(): string
{
    $c = cfg();
    // Default: next to public_html, so files are never reachable from the web.
    $dir = $c['data_dir'] ?? dirname(__DIR__, 3) . '/tprint-data';
    if (!is_dir($dir . '/uploads') && !mkdir($dir . '/uploads', 0700, true) && !is_dir($dir . '/uploads')) {
        fail(500, 'Storage unavailable.');
    }
    return $dir;
}

function db(): PDO
{
    static $pdo = null;
    if ($pdo === null) {
        $c = cfg();
        try {
            $pdo = new PDO($c['db_dsn'], $c['db_user'] ?? null, $c['db_pass'] ?? null, [
                PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
                PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
                PDO::ATTR_EMULATE_PREPARES => false,
            ]);
        } catch (PDOException $e) {
            // e.g. 1045 = wrong password in _private/config.php (changed in hPanel but not here).
            error_log('[print.tfix.in] database connection failed: error ' . $e->getCode()
                . ' - check db_user/db_pass in _private/config.php against hPanel > Databases');
            $pdo = null;
            fail(503, SERVICE_PAUSED);
        }
        ensure_schema($pdo);
    }
    return $pdo;
}

function ensure_schema(PDO $pdo): void
{
    $flag = data_dir() . '/.schema-v6';
    if (is_file($flag)) {
        return;
    }
    $mysql = $pdo->getAttribute(PDO::ATTR_DRIVER_NAME) === 'mysql';
    $pk = $mysql ? 'INT UNSIGNED AUTO_INCREMENT PRIMARY KEY' : 'INTEGER PRIMARY KEY AUTOINCREMENT';
    $tail = $mysql ? ' ENGINE=InnoDB DEFAULT CHARSET=utf8mb4' : '';
    $pdo->exec("CREATE TABLE IF NOT EXISTS orders (
        id $pk,
        code VARCHAR(12) NOT NULL UNIQUE,
        token_hash CHAR(64) NOT NULL,
        phone VARCHAR(15) NOT NULL,
        name VARCHAR(60) NOT NULL,
        notes VARCHAR(400) NOT NULL DEFAULT '',
        status VARCHAR(20) NOT NULL DEFAULT 'pending',
        est_total DECIMAL(10,2) NULL,
        confirmed_total DECIMAL(10,2) NULL,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        fetched_at DATETIME NULL
    )$tail");
    $pdo->exec("CREATE TABLE IF NOT EXISTS order_files (
        id $pk,
        order_id INT NOT NULL,
        original_name VARCHAR(200) NOT NULL,
        ext VARCHAR(8) NOT NULL,
        stored_name VARCHAR(64) NOT NULL,
        size_bytes INT NOT NULL,
        color TINYINT NOT NULL DEFAULT 0,
        doubleside TINYINT NOT NULL DEFAULT 0,
        duplex_edge VARCHAR(8) NOT NULL DEFAULT 'long',
        paper_size VARCHAR(4) NOT NULL DEFAULT 'A4',
        orientation VARCHAR(10) NOT NULL DEFAULT 'portrait',
        pages_per_sheet TINYINT NOT NULL DEFAULT 1,
        copies SMALLINT NOT NULL DEFAULT 1,
        pages_range VARCHAR(100) NOT NULL DEFAULT '',
        est_pages INT NULL,
        job_id INT NULL,
        pages INT NULL,
        cost DECIMAL(10,2) NULL,
        job_status VARCHAR(20) NULL,
        paid TINYINT NOT NULL DEFAULT 0,
        error VARCHAR(200) NULL,
        file_deleted TINYINT NOT NULL DEFAULT 0
    )$tail");
    // v3: CLUTCH Rewards ledger. The mobile number is the customer's rewards ID.
    // One credit per collected order (order_id is the key, so it is never counted twice).
    $pdo->exec("CREATE TABLE IF NOT EXISTS reward_credits (
        order_id INT NOT NULL PRIMARY KEY,
        phone VARCHAR(15) NOT NULL,
        amount DECIMAL(10,2) NOT NULL,
        reward DECIMAL(10,2) NOT NULL,
        created_at DATETIME NOT NULL
    )$tail");
    // Rewards used at the counter (entered by staff in T Print admin).
    $pdo->exec("CREATE TABLE IF NOT EXISTS reward_redemptions (
        id $pk,
        phone VARCHAR(15) NOT NULL,
        amount DECIMAL(10,2) NOT NULL,
        note VARCHAR(100) NOT NULL DEFAULT '',
        created_at DATETIME NOT NULL
    )$tail");
    // v4: tamper-evident log of every credit and use. Each entry stores the SHA-256 of the
    // previous one, so changing or deleting any past entry breaks the chain (see ledger_verify).
    $pdo->exec("CREATE TABLE IF NOT EXISTS reward_ledger (
        seq INT NOT NULL PRIMARY KEY,
        phone VARCHAR(15) NOT NULL,
        kind VARCHAR(10) NOT NULL,
        order_id INT NULL,
        amount DECIMAL(10,2) NOT NULL,
        points DECIMAL(10,2) NOT NULL,
        balance_after DECIMAL(10,2) NOT NULL,
        note VARCHAR(100) NOT NULL DEFAULT '',
        created_at DATETIME NOT NULL,
        prev_hash CHAR(64) NOT NULL,
        hash CHAR(64) NOT NULL
    )$tail");
    foreach (['CREATE INDEX idx_credit_phone ON reward_credits (phone)', 'CREATE INDEX idx_redeem_phone ON reward_redemptions (phone)',
              'CREATE INDEX idx_ledger_phone ON reward_ledger (phone)'] as $sql) {
        try {
            $pdo->exec($sql);
        } catch (PDOException $e) {
            // already there
        }
    }
    // v5: repair.tfix.in bookings (same customer = same mobile number, same CLUTCH Rewards).
    $pdo->exec("CREATE TABLE IF NOT EXISTS repairs (
        id $pk,
        code VARCHAR(12) NOT NULL UNIQUE,
        token_hash CHAR(64) NOT NULL,
        phone VARCHAR(15) NOT NULL,
        name VARCHAR(60) NOT NULL,
        device_type VARCHAR(12) NOT NULL,
        brand VARCHAR(40) NOT NULL DEFAULT '',
        model VARCHAR(60) NOT NULL DEFAULT '',
        issues VARCHAR(300) NOT NULL DEFAULT '',
        description VARCHAR(600) NOT NULL DEFAULT '',
        visit_date DATE NULL,
        diagnosis TEXT NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'requested',
        quote DECIMAL(10,2) NULL,
        final_amount DECIMAL(10,2) NULL,
        paid TINYINT NOT NULL DEFAULT 0,
        staff_note VARCHAR(300) NOT NULL DEFAULT '',
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL
    )$tail");
    $pdo->exec("CREATE TABLE IF NOT EXISTS repair_credits (
        repair_id INT NOT NULL PRIMARY KEY,
        phone VARCHAR(15) NOT NULL,
        amount DECIMAL(10,2) NOT NULL,
        reward DECIMAL(10,2) NOT NULL,
        created_at DATETIME NOT NULL
    )$tail");
    foreach (['CREATE INDEX idx_repair_phone ON repairs (phone)', 'CREATE INDEX idx_rcredit_phone ON repair_credits (phone)'] as $sql) {
        try {
            $pdo->exec($sql);
        } catch (PDOException $e) {
            // already there
        }
    }
    // v6: green counter (blank pages the customer skipped), group/class orders, ready alerts (web push).
    foreach (['ALTER TABLE order_files ADD COLUMN blank_skipped INT NOT NULL DEFAULT 0',
              'ALTER TABLE orders ADD COLUMN group_id INT NULL'] as $sql) {
        try {
            $pdo->exec($sql);
        } catch (PDOException $e) {
            // column already there
        }
    }
    $pdo->exec("CREATE TABLE IF NOT EXISTS print_groups (
        id $pk,
        code VARCHAR(12) NOT NULL UNIQUE,
        admin_hash CHAR(64) NOT NULL,
        title VARCHAR(80) NOT NULL,
        rep_name VARCHAR(60) NOT NULL,
        rep_phone VARCHAR(15) NOT NULL,
        note VARCHAR(200) NOT NULL DEFAULT '',
        deadline DATE NULL,
        status VARCHAR(10) NOT NULL DEFAULT 'open',
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL
    )$tail");
    $pdo->exec("CREATE TABLE IF NOT EXISTS push_subs (
        id $pk,
        order_id INT NOT NULL,
        endpoint_hash CHAR(64) NOT NULL,
        endpoint VARCHAR(700) NOT NULL,
        created_at DATETIME NOT NULL,
        notified_at DATETIME NULL
    )$tail");
    foreach (['CREATE INDEX idx_orders_group ON orders (group_id)', 'CREATE UNIQUE INDEX idx_push_unique ON push_subs (order_id, endpoint_hash)',
              'CREATE INDEX idx_push_endpoint ON push_subs (endpoint_hash)'] as $sql) {
        try {
            $pdo->exec($sql);
        } catch (PDOException $e) {
            // already there
        }
    }
    $pdo->exec("CREATE TABLE IF NOT EXISTS rate_events (
        id $pk,
        ip VARCHAR(45) NOT NULL,
        created_at DATETIME NOT NULL
    )$tail");
    ledger_backfill($pdo);
    @file_put_contents($flag, gmdate('c'));
}

function now(): string
{
    return gmdate('Y-m-d H:i:s');
}

/** Database time (UTC) -> ISO 8601, e.g. 2026-11-22T15:15:18Z */
function iso(?string $t): ?string
{
    return $t === null ? null : str_replace(' ', 'T', substr($t, 0, 19)) . 'Z';
}

function send_json(int $status, array $data): void
{
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store');
    header('X-Content-Type-Options: nosniff');
    echo json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

function fail(int $status, string $message): void
{
    send_json($status, ['error' => $message]);
}

function require_method(string $method): void
{
    if (($_SERVER['REQUEST_METHOD'] ?? '') !== $method) {
        fail(405, 'Method not allowed.');
    }
}

function client_ip(): string
{
    return substr((string)($_SERVER['REMOTE_ADDR'] ?? 'unknown'), 0, 45);
}

/** Indian mobile numbers only: returns the 10-digit number or null. */
function normalize_phone(string $raw): ?string
{
    $digits = preg_replace('/\D+/', '', $raw) ?? '';
    if (strlen($digits) === 12 && str_starts_with($digits, '91')) {
        $digits = substr($digits, 2);
    } elseif (strlen($digits) === 11 && str_starts_with($digits, '0')) {
        $digits = substr($digits, 1);
    }
    return preg_match('/^[6-9]\d{9}$/', $digits) ? $digits : null;
}

function clean_text(string $s, int $max): string
{
    $s = preg_replace('/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/u', '', $s) ?? '';
    return mb_substr(trim($s), 0, $max);
}

function random_code(): string
{
    $alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
    $out = '';
    for ($i = 0; $i < 6; $i++) {
        $out .= $alphabet[random_int(0, strlen($alphabet) - 1)];
    }
    return 'TMP' . $out;   // placeholder until the order id is known (see order.php)
}

/** Check the first bytes so a renamed file can't pass as a PDF/image/document. */
function sniff_ok(string $path, string $ext): bool
{
    $h = @fopen($path, 'rb');
    if (!$h) {
        return false;
    }
    $head = (string)fread($h, 8);
    fclose($h);
    return match ($ext) {
        'pdf' => str_starts_with($head, '%PDF'),
        'jpg', 'jpeg' => str_starts_with($head, "\xFF\xD8\xFF"),
        'png' => str_starts_with($head, "\x89PNG"),
        'docx', 'pptx' => str_starts_with($head, "PK\x03\x04"),
        'ppt' => str_starts_with($head, "\xD0\xCF\x11\xE0"),
        default => false,
    };
}

function valid_pages_range(string $r): bool
{
    $r = str_replace(' ', '', $r);
    return $r === '' || (strlen($r) <= 100 && preg_match('/^\d+(-\d+)?(,\d+(-\d+)?)*$/', $r) === 1);
}

/** Customer-facing view of an order: no phone number, no stored file names. */
function public_order(array $o, array $files): array
{
    return [
        'code' => $o['code'],
        'name' => $o['name'],
        'status' => $o['status'],
        'est_total' => $o['est_total'] !== null ? (float)$o['est_total'] : null,
        'confirmed_total' => $o['confirmed_total'] !== null ? (float)$o['confirmed_total'] : null,
        'created_at' => iso($o['created_at']),
        'updated_at' => iso($o['updated_at']),
        'sheets_saved' => sheets_saved_order($files),
        'group' => order_group($o),
        'files' => array_map(fn($f) => [
            'name' => $f['original_name'],
            'color' => (bool)$f['color'],
            'doubleside' => (bool)$f['doubleside'],
            'paper_size' => $f['paper_size'],
            'pages_per_sheet' => (int)$f['pages_per_sheet'],
            'copies' => (int)$f['copies'],
            'pages_range' => $f['pages_range'],
            'pages' => $f['pages'] !== null ? (int)$f['pages'] : null,
            'cost' => $f['cost'] !== null ? (float)$f['cost'] : null,
            'job_status' => $f['job_status'],
            'paid' => (bool)$f['paid'],
            'error' => $f['error'],
        ], $files),
    ];
}

function order_files(int $orderId): array
{
    $st = db()->prepare('SELECT * FROM order_files WHERE order_id = ? ORDER BY id');
    $st->execute([$orderId]);
    return $st->fetchAll();
}

function delete_stored_file(array $f): void
{
    if ((int)$f['file_deleted'] === 1) {
        return;
    }
    $path = data_dir() . '/uploads/' . basename($f['stored_name']);
    if (is_file($path)) {
        @unlink($path);
    }
    db()->prepare('UPDATE order_files SET file_deleted = 1 WHERE id = ?')->execute([$f['id']]);
}


/* ---------------- CLUTCH Rewards (mobile number = rewards ID, no password) ---------------- */

/** ₹5 for every full ₹100 of total spend. */
function rewards_earned_for(float $spent): float
{
    return floor(round($spent, 2) / REWARD_STEP + 1e-9) * REWARD_PER_STEP;
}

function rewards_for(string $phone): array
{
    $pdo = db();
    // Print orders and repairs both count towards the same rewards.
    $st = $pdo->prepare('SELECT COALESCE(SUM(amount), 0), COALESCE(SUM(reward), 0) FROM (
                             SELECT amount, reward FROM reward_credits WHERE phone = ?
                             UNION ALL SELECT amount, reward FROM repair_credits WHERE phone = ?) t');
    $st->execute([$phone, $phone]);
    [$spent, $earned] = array_map('floatval', $st->fetch(PDO::FETCH_NUM));
    $st = $pdo->prepare('SELECT COALESCE(SUM(amount), 0) FROM reward_redemptions WHERE phone = ?');
    $st->execute([$phone]);
    $redeemed = (float)$st->fetchColumn();
    $into = fmod(round($spent, 2), REWARD_STEP);
    return [
        'phone' => masked_phone($phone),
        'spent' => round($spent, 2),
        'earned' => round($earned, 2),
        'redeemed' => round($redeemed, 2),
        'balance' => round(max(0, $earned - $redeemed), 2),
        'step' => REWARD_STEP,
        'per_step' => REWARD_PER_STEP,
        'next_in' => round(REWARD_STEP - $into, 2),
    ];
}

/** Reward added by one order (0 until it is collected). */
function order_reward(int $orderId): float
{
    $st = db()->prepare('SELECT reward FROM reward_credits WHERE order_id = ?');
    $st->execute([$orderId]);
    $r = $st->fetchColumn();
    return $r === false ? 0.0 : (float)$r;
}

/**
 * Credit a collected order's paid print cost to the customer's rewards.
 * Safe to call repeatedly: each order is credited once.
 */
function credit_order(int $orderId): void
{
    $pdo = db();
    $st = $pdo->prepare('SELECT * FROM orders WHERE id = ?');
    $st->execute([$orderId]);
    $o = $st->fetch();
    if (!$o || $o['status'] !== 'collected') {
        return;
    }
    $amount = 0.0;
    foreach (order_files($orderId) as $f) {
        if ((int)$f['paid'] === 1 && $f['error'] === null && $f['cost'] !== null) {
            $amount += (float)$f['cost'];
        }
    }
    $amount = round($amount, 2);
    if ($amount <= 0) {
        return;
    }
    $pdo->beginTransaction();
    try {
        $chk = $pdo->prepare('SELECT 1 FROM reward_credits WHERE order_id = ?');
        $chk->execute([$orderId]);
        if (!$chk->fetchColumn()) {
            $before = rewards_for($o['phone'])['spent'];
            $reward = rewards_earned_for($before + $amount) - rewards_earned_for($before);
            $pdo->prepare('INSERT INTO reward_credits (order_id, phone, amount, reward, created_at) VALUES (?, ?, ?, ?, ?)')
                ->execute([$orderId, $o['phone'], $amount, $reward, now()]);
            ledger_append($o['phone'], 'credit', $orderId, $amount, $reward, $o['code']);
        }
        $pdo->commit();
    } catch (Throwable $e) {
        if ($pdo->inTransaction()) {
            $pdo->rollBack();
        }
        error_log('[tprint rewards] ' . $e->getMessage());
    }
}

/* ---------------- tamper-evident rewards log (hash chain) ---------------- */

const LEDGER_GENESIS = '0000000000000000000000000000000000000000000000000000000000000000';

function ledger_hash(array $e): string
{
    return hash('sha256', implode('|', [
        (int)$e['seq'], $e['phone'], $e['kind'], $e['order_id'] === null ? '' : (int)$e['order_id'],
        number_format((float)$e['amount'], 2, '.', ''), number_format((float)$e['points'], 2, '.', ''),
        number_format((float)$e['balance_after'], 2, '.', ''), $e['note'], substr((string)$e['created_at'], 0, 19), $e['prev_hash'],
    ]));
}

/**
 * Add one entry to the log. Call inside the transaction that changed the rewards, after
 * the change, so balance_after is the customer's new balance.
 */
function ledger_append(string $phone, string $kind, ?int $orderId, float $amount, float $points, string $note, ?string $at = null): array
{
    $pdo = db();
    $lock = $pdo->getAttribute(PDO::ATTR_DRIVER_NAME) === 'mysql' ? ' FOR UPDATE' : '';
    $last = $pdo->query('SELECT seq, hash FROM reward_ledger ORDER BY seq DESC LIMIT 1' . $lock)->fetch();
    $e = [
        'seq' => $last ? (int)$last['seq'] + 1 : 1, 'phone' => $phone, 'kind' => $kind, 'order_id' => $orderId,
        'amount' => round($amount, 2), 'points' => round($points, 2), 'balance_after' => rewards_for($phone)['balance'],
        'note' => clean_text($note, 100), 'created_at' => $at ?? now(), 'prev_hash' => $last ? $last['hash'] : LEDGER_GENESIS,
    ];
    $e['hash'] = ledger_hash($e);
    $pdo->prepare('INSERT INTO reward_ledger (seq, phone, kind, order_id, amount, points, balance_after, note, created_at, prev_hash, hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)')
        ->execute([$e['seq'], $e['phone'], $e['kind'], $e['order_id'], $e['amount'], $e['points'], $e['balance_after'],
                   $e['note'], $e['created_at'], $e['prev_hash'], $e['hash']]);
    return $e;
}

/** Rewards given before the log existed are written into it once, oldest first. */
function ledger_backfill(PDO $pdo): void
{
    if ((int)$pdo->query('SELECT COUNT(*) FROM reward_ledger')->fetchColumn() > 0) {
        return;
    }
    $rows = $pdo->query("SELECT c.phone, 'credit' AS kind, c.order_id, c.amount, c.reward AS points, COALESCE(o.code, '') AS note, c.created_at
                         FROM reward_credits c LEFT JOIN orders o ON o.id = c.order_id
                         UNION ALL
                         SELECT c.phone, 'credit', NULL, c.amount, c.reward, COALESCE(r.code, ''), c.created_at
                         FROM repair_credits c LEFT JOIN repairs r ON r.id = c.repair_id
                         UNION ALL
                         SELECT phone, 'redeem', NULL, amount, -amount, note, created_at FROM reward_redemptions
                         ORDER BY created_at")->fetchAll();
    if (!$rows) {
        return;
    }
    // Balances are rebuilt step by step, so each entry shows the balance at that time.
    $bal = [];
    $seq = 0;
    $prev = LEDGER_GENESIS;
    $ins = $pdo->prepare('INSERT INTO reward_ledger (seq, phone, kind, order_id, amount, points, balance_after, note, created_at, prev_hash, hash)
                          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)');
    foreach ($rows as $r) {
        $bal[$r['phone']] = round(($bal[$r['phone']] ?? 0) + (float)$r['points'], 2);
        $e = ['seq' => ++$seq, 'phone' => $r['phone'], 'kind' => $r['kind'], 'order_id' => $r['order_id'] === null ? null : (int)$r['order_id'],
              'amount' => round((float)$r['amount'], 2), 'points' => round((float)$r['points'], 2), 'balance_after' => max(0, $bal[$r['phone']]),
              'note' => clean_text((string)$r['note'], 100), 'created_at' => substr((string)$r['created_at'], 0, 19), 'prev_hash' => $prev];
        $e['hash'] = $prev = ledger_hash($e);
        $ins->execute([$e['seq'], $e['phone'], $e['kind'], $e['order_id'], $e['amount'], $e['points'], $e['balance_after'],
                       $e['note'], $e['created_at'], $e['prev_hash'], $e['hash']]);
    }
}

/**
 * Check the whole log: every entry's hash, every link to the previous entry, and that the
 * log's totals match the rewards tables. Returns {ok, entries, head, problem}.
 */
function ledger_verify(): array
{
    $pdo = db();
    $prev = LEDGER_GENESIS;
    $n = 0;
    $credit = 0.0;
    $used = 0.0;
    $st = $pdo->query('SELECT * FROM reward_ledger ORDER BY seq');
    while ($e = $st->fetch()) {
        $n++;
        if ((int)$e['seq'] !== $n) {
            return ['ok' => false, 'entries' => $n, 'head' => $prev, 'problem' => "entry $n is missing"];
        }
        if ($e['prev_hash'] !== $prev || !hash_equals($e['hash'], ledger_hash($e))) {
            return ['ok' => false, 'entries' => $n, 'head' => $prev, 'problem' => "entry $n was changed"];
        }
        $prev = $e['hash'];
        if ($e['kind'] === 'credit') {
            $credit += (float)$e['points'];
        } else {
            $used += (float)$e['amount'];
        }
    }
    $tc = (float)$pdo->query('SELECT COALESCE(SUM(reward), 0) FROM reward_credits')->fetchColumn()
        + (float)$pdo->query('SELECT COALESCE(SUM(reward), 0) FROM repair_credits')->fetchColumn();
    $tu = (float)$pdo->query('SELECT COALESCE(SUM(amount), 0) FROM reward_redemptions')->fetchColumn();
    if (abs($tc - $credit) > 0.009 || abs($tu - $used) > 0.009) {
        return ['ok' => false, 'entries' => $n, 'head' => $prev, 'problem' => 'rewards totals do not match the log'];
    }
    return ['ok' => true, 'entries' => $n, 'head' => $prev, 'problem' => null];
}

/** A customer's log entries, newest first (for the pickup pass and staff). */
function ledger_for(string $phone, int $limit = 20): array
{
    $st = db()->prepare('SELECT l.*, o.code FROM reward_ledger l LEFT JOIN orders o ON o.id = l.order_id
                         WHERE l.phone = ? ORDER BY l.seq DESC LIMIT ' . max(1, min(100, $limit)));
    $st->execute([$phone]);
    return array_map(fn($e) => [
        'seq' => (int)$e['seq'], 'kind' => $e['kind'],
        'code' => $e['code'] ?? ($e['kind'] === 'credit' && $e['note'] !== '' ? $e['note'] : null), 'amount' => (float)$e['amount'],
        'points' => (float)$e['points'], 'balance' => (float)$e['balance_after'], 'note' => $e['note'],
        'at' => iso($e['created_at']), 'receipt' => substr($e['hash'], 0, 12),
    ], $st->fetchAll());
}

function order_group(array $o): ?array
{
    if (empty($o['group_id'])) {
        return null;
    }
    $st = db()->prepare('SELECT code, title FROM print_groups WHERE id = ?');
    $st->execute([(int)$o['group_id']]);
    $g = $st->fetch();
    return $g ?: null;
}

/* ---------------- ready alerts: web push (no app, no payload) ---------------- */

// Only the browsers' own push services may receive our requests (no other hosts: no SSRF).
const PUSH_HOSTS = '/^(fcm\.googleapis\.com|updates\.push\.services\.mozilla\.com|[a-z0-9.-]+\.push\.services\.mozilla\.com|web\.push\.apple\.com|[a-z0-9-]+\.notify\.windows\.com)$/';

function b64url(string $bin): string
{
    return rtrim(strtr(base64_encode($bin), '+/', '-_'), '=');
}

function push_endpoint_ok(string $endpoint): bool
{
    if (strlen($endpoint) > 700) {
        return false;
    }
    $u = parse_url($endpoint);
    if (!$u || empty($u['host'])) {
        return false;
    }
    $test = cfg()['push_test_hosts'] ?? [];   // only set in local tests
    if (in_array(($u['host'] ?? '') . ':' . ($u['port'] ?? ''), (array)$test, true) && ($u['scheme'] ?? '') === 'http') {
        return true;
    }
    return ($u['scheme'] ?? '') === 'https' && empty($u['port']) && preg_match(PUSH_HOSTS, strtolower($u['host'])) === 1;
}

/** VAPID key pair (P-256), created once and kept outside the web root. */
function vapid_keys(): array
{
    $path = data_dir() . '/vapid.pem';
    if (!is_file($path)) {
        $k = openssl_pkey_new(['curve_name' => 'prime256v1', 'private_key_type' => OPENSSL_KEYTYPE_EC]);
        openssl_pkey_export($k, $pem);
        $tmp = $path . '.' . bin2hex(random_bytes(4));
        file_put_contents($tmp, $pem);
        @chmod($tmp, 0600);
        if (!@rename($tmp, $path)) {
            @unlink($tmp);
        }
    }
    $key = openssl_pkey_get_private((string)file_get_contents($path));
    $d = openssl_pkey_get_details($key)['ec'];
    $pub = "\x04" . str_pad($d['x'], 32, "\0", STR_PAD_LEFT) . str_pad($d['y'], 32, "\0", STR_PAD_LEFT);
    return ['key' => $key, 'public' => b64url($pub)];
}

/** DER ECDSA signature -> raw 64-byte r||s (what JWT ES256 needs). */
function der_to_raw(string $der): string
{
    $pos = 2;
    if (ord($der[1]) & 0x80) {
        $pos += ord($der[1]) & 0x7f;
    }
    $out = '';
    for ($i = 0; $i < 2; $i++) {
        $len = ord($der[$pos + 1]);
        $int = substr($der, $pos + 2, $len);
        $out .= str_pad(ltrim($int, "\0"), 32, "\0", STR_PAD_LEFT);
        $pos += 2 + $len;
    }
    return $out;
}

function vapid_header(string $endpoint): string
{
    $u = parse_url($endpoint);
    $keys = vapid_keys();
    $aud = $u['scheme'] . '://' . $u['host'] . (isset($u['port']) ? ':' . $u['port'] : '');
    $data = b64url(json_encode(['typ' => 'JWT', 'alg' => 'ES256'])) . '.'
        . b64url(json_encode(['aud' => $aud, 'exp' => time() + 12 * 3600, 'sub' => 'https://print.tfix.in/']));
    openssl_sign($data, $sig, $keys['key'], OPENSSL_ALGO_SHA256);
    return 'vapid t=' . $data . '.' . b64url(der_to_raw($sig)) . ', k=' . $keys['public'];
}

/** Send one empty push. Returns the HTTP status (0 on network error). */
function push_send(string $endpoint): int
{
    if (!push_endpoint_ok($endpoint) || !function_exists('curl_init')) {
        return 0;
    }
    $ch = curl_init($endpoint);
    curl_setopt_array($ch, [
        CURLOPT_POST => true, CURLOPT_POSTFIELDS => '', CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT => 6, CURLOPT_CONNECTTIMEOUT => 4, CURLOPT_FOLLOWLOCATION => false,
        CURLOPT_PROTOCOLS => CURLPROTO_HTTPS | CURLPROTO_HTTP,
        CURLOPT_HTTPHEADER => ['TTL: 86400', 'Urgency: high', 'Content-Length: 0', 'Authorization: ' . vapid_header($endpoint)],
    ]);
    curl_exec($ch);
    $code = (int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
    curl_close($ch);
    return $code;
}

/** Tell every phone that asked for an alert that this order is ready (once each). */
function notify_ready(int $orderId): int
{
    $pdo = db();
    $st = $pdo->prepare('SELECT * FROM push_subs WHERE order_id = ? AND notified_at IS NULL');
    $st->execute([$orderId]);
    $sent = 0;
    foreach ($st->fetchAll() as $sub) {
        $code = push_send($sub['endpoint']);
        if ($code >= 200 && $code < 300) {
            $pdo->prepare('UPDATE push_subs SET notified_at = ? WHERE id = ?')->execute([now(), (int)$sub['id']]);
            $sent++;
        } elseif ($code === 404 || $code === 410) {
            $pdo->prepare('DELETE FROM push_subs WHERE id = ?')->execute([(int)$sub['id']]);   // unsubscribed
        } else {
            error_log('[tprint push] ' . $code . ' for subscription ' . $sub['id']);
        }
    }
    return $sent;
}

/* ---------------- green counter: paper saved ---------------- */

const SHEET_GRAMS = ['A4' => 4.99, 'A3' => 9.98];   // 80 gsm paper

/**
 * Sheets one file saves compared with printing every page one-sided, one page per sheet:
 * double-sided, several pages per sheet, and blank pages the customer skipped (Smart print check).
 * Uses the page count the shop measured (T Print), not the customer's browser.
 */
function sheets_saved_file(array $f): int
{
    if ($f['pages'] === null || $f['error'] !== null) {
        return 0;
    }
    $pages = max(0, (int)$f['pages']);
    $copies = max(1, (int)$f['copies']);
    $pps = max(1, (int)$f['pages_per_sheet']);
    $printed = (int)ceil($pages / $pps);
    $actual = (int)$f['doubleside'] ? (int)ceil($printed / 2) : $printed;
    $baseline = $pages + min(max(0, (int)($f['blank_skipped'] ?? 0)), 2000);
    return max(0, ($baseline - $actual) * $copies);
}

function sheets_saved_order(array $files): int
{
    return array_sum(array_map('sheets_saved_file', $files));
}

/** Totals over collected orders (cached for 5 minutes). */
function green_stats(): array
{
    $cache = data_dir() . '/green-stats.json';
    if (is_file($cache) && filemtime($cache) > time() - 300) {
        $c = json_decode((string)file_get_contents($cache), true);
        if (is_array($c)) {
            return $c;
        }
    }
    $rows = db()->query("SELECT f.* FROM order_files f JOIN orders o ON o.id = f.order_id
                         WHERE o.status = 'collected' AND f.paid = 1")->fetchAll();
    $sheets = 0;
    $grams = 0.0;
    $orders = [];
    foreach ($rows as $f) {
        $n = sheets_saved_file($f);
        $sheets += $n;
        $grams += $n * (SHEET_GRAMS[$f['paper_size']] ?? SHEET_GRAMS['A4']);
        $orders[(int)$f['order_id']] = true;
    }
    $out = ['sheets_saved' => $sheets, 'paper_kg' => round($grams / 1000, 1), 'orders' => count($orders), 'at' => gmdate('c')];
    @file_put_contents($cache, json_encode($out), LOCK_EX);
    return $out;
}

/* ---------------- repairs (repair.tfix.in) ---------------- */

const REPAIR_STATUSES = ['requested', 'received', 'repairing', 'waiting_parts', 'ready', 'collected', 'cancelled'];
const REPAIR_DEVICES = ['phone', 'laptop', 'tablet', 'other'];
const REPAIR_ISSUES = ['screen', 'battery', 'charging', 'speaker', 'mic', 'camera', 'water', 'buttons', 'software', 'network', 'other'];

function public_repair(array $r): array
{
    return [
        'code' => $r['code'], 'name' => $r['name'], 'status' => $r['status'],
        'device_type' => $r['device_type'], 'brand' => $r['brand'], 'model' => $r['model'],
        'issues' => $r['issues'] === '' ? [] : explode(',', $r['issues']), 'description' => $r['description'],
        'visit_date' => $r['visit_date'], 'has_diagnosis' => $r['diagnosis'] !== null && $r['diagnosis'] !== '',
        'quote' => $r['quote'] !== null ? (float)$r['quote'] : null,
        'final_amount' => $r['final_amount'] !== null ? (float)$r['final_amount'] : null,
        'paid' => (bool)$r['paid'], 'note' => $r['staff_note'],
        'created_at' => iso($r['created_at']), 'updated_at' => iso($r['updated_at']),
    ];
}

function repair_reward(int $repairId): float
{
    $st = db()->prepare('SELECT reward FROM repair_credits WHERE repair_id = ?');
    $st->execute([$repairId]);
    $r = $st->fetchColumn();
    return $r === false ? 0.0 : (float)$r;
}

/** Credit a collected, paid repair to the customer's rewards (once). */
function credit_repair(int $repairId): void
{
    $pdo = db();
    $st = $pdo->prepare('SELECT * FROM repairs WHERE id = ?');
    $st->execute([$repairId]);
    $r = $st->fetch();
    if (!$r || $r['status'] !== 'collected' || (int)$r['paid'] !== 1 || $r['final_amount'] === null) {
        return;
    }
    $amount = round((float)$r['final_amount'], 2);
    if ($amount <= 0) {
        return;
    }
    $pdo->beginTransaction();
    try {
        $chk = $pdo->prepare('SELECT 1 FROM repair_credits WHERE repair_id = ?');
        $chk->execute([$repairId]);
        if (!$chk->fetchColumn()) {
            $before = rewards_for($r['phone'])['spent'];
            $reward = rewards_earned_for($before + $amount) - rewards_earned_for($before);
            $pdo->prepare('INSERT INTO repair_credits (repair_id, phone, amount, reward, created_at) VALUES (?, ?, ?, ?, ?)')
                ->execute([$repairId, $r['phone'], $amount, $reward, now()]);
            ledger_append($r['phone'], 'credit', null, $amount, $reward, $r['code']);
        }
        $pdo->commit();
    } catch (Throwable $e) {
        if ($pdo->inTransaction()) {
            $pdo->rollBack();
        }
        error_log('[tprint repair rewards] ' . $e->getMessage());
    }
}

function masked_phone(string $phone): string
{
    return substr($phone, 0, 2) . '******' . substr($phone, -2);
}
