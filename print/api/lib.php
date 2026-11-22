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
    $flag = data_dir() . '/.schema-v1';
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
    $pdo->exec("CREATE TABLE IF NOT EXISTS rate_events (
        id $pk,
        ip VARCHAR(45) NOT NULL,
        created_at DATETIME NOT NULL
    )$tail");
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
    return 'TP' . $out;
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
