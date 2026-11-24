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

function cfg(): array
{
    static $c = null;
    if ($c === null) {
        $path = getenv('TPRINT_CLOUD_CONFIG') ?: dirname(__DIR__) . '/_private/config.php';
        if (!is_file($path)) {
            fail(503, 'Online ordering is not configured yet.');
        }
        $c = require $path;
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
        $pdo = new PDO($c['db_dsn'], $c['db_user'] ?? null, $c['db_pass'] ?? null, [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
            PDO::ATTR_EMULATE_PREPARES => false,
        ]);
        ensure_schema($pdo);
    }
    return $pdo;
}

function ensure_schema(PDO $pdo): void
{
    $flag = data_dir() . '/.schema-v4';
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
    $st = $pdo->prepare('SELECT COALESCE(SUM(amount), 0), COALESCE(SUM(reward), 0) FROM reward_credits WHERE phone = ?');
    $st->execute([$phone]);
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
            $sum = $pdo->prepare('SELECT COALESCE(SUM(amount), 0) FROM reward_credits WHERE phone = ?');
            $sum->execute([$o['phone']]);
            $before = (float)$sum->fetchColumn();
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
    $tc = (float)$pdo->query('SELECT COALESCE(SUM(reward), 0) FROM reward_credits')->fetchColumn();
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
        'seq' => (int)$e['seq'], 'kind' => $e['kind'], 'code' => $e['code'], 'amount' => (float)$e['amount'],
        'points' => (float)$e['points'], 'balance' => (float)$e['balance_after'], 'note' => $e['note'],
        'at' => iso($e['created_at']), 'receipt' => substr($e['hash'], 0, 12),
    ], $st->fetchAll());
}

function masked_phone(string $phone): string
{
    return substr($phone, 0, 2) . '******' . substr($phone, -2);
}
