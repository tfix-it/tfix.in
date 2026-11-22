<?php
/**
 * T-PRINT on Hostinger: shared bootstrap.
 *
 * Every request enters through api/index.php, which loads this file. It reads
 * the private config (kept OUTSIDE public_html, see tprint-config.sample.php),
 * opens the database and provides the small request/response helpers the
 * route files use.
 */
declare(strict_types=1);

const TPRINT_VERSION = '3.0.0';

final class HttpError extends RuntimeException
{
    public function __construct(public readonly int $status, string $detail)
    {
        parent::__construct($detail);
    }
}

/** A print/payment rule refused the action (maps to HTTP 409). */
final class PrintBlocked extends RuntimeException {}

function tprint_config(): array
{
    static $config = null;
    if ($config !== null) {
        return $config;
    }
    // public_html/TPRINT/lib -> domains/tfix.in/tprint-config.php (not web-reachable)
    $path = getenv('TPRINT_CONFIG') ?: dirname(__DIR__, 3) . '/tprint-config.php';
    if (!is_file($path)) {
        throw new HttpError(500, 'T-PRINT is not configured yet: tprint-config.php is missing.');
    }
    $loaded = require $path;
    $config = array_replace([
        'public_origin' => 'https://print.tfix.in',
        'storage_dir' => dirname($path) . '/tprint-storage',
        'admin_username' => 'admin',
        'setup_code' => '',
        'agent_key' => '',
        'mcp_token' => '',
        'mcp_allow_print' => false,
        'timezone' => 'Asia/Kolkata',
        'trust_forwarded_for' => true,
        'agent_offline_after_sec' => 45,
    ], $loaded);
    return $config;
}

function storage_path(string $name = ''): string
{
    $dir = rtrim(tprint_config()['storage_dir'], '/');
    if (!is_dir($dir) && !mkdir($dir, 0750, true) && !is_dir($dir)) {
        throw new HttpError(500, 'Storage folder is not writable.');
    }
    if ($name === '') {
        return $dir;
    }
    if (!preg_match('/^[A-Za-z0-9._-]+$/', $name) || str_contains($name, '..')) {
        throw new HttpError(400, 'Bad file name');
    }
    return $dir . '/' . $name;
}

// ------------------------------------------------------------------ time
/** UTC timestamp with microseconds, same shape the Python version stored. */
function now_utc(): string
{
    return (new DateTimeImmutable('now', new DateTimeZone('UTC')))->format('Y-m-d H:i:s.u');
}

/** UTC start of "today" in the shop's time zone (for daily job numbering). */
function shop_day_start_utc(): string
{
    $tz = new DateTimeZone(tprint_config()['timezone']);
    $midnight = new DateTimeImmutable('today', $tz);
    return $midnight->setTimezone(new DateTimeZone('UTC'))->format('Y-m-d H:i:s.u');
}

function iso(?string $dbTime): ?string
{
    return $dbTime === null ? null : str_replace(' ', 'T', $dbTime);
}

// ------------------------------------------------------------------ request
function request_method(): string
{
    return strtoupper($_SERVER['REQUEST_METHOD'] ?? 'GET');
}

function request_path(): string
{
    $path = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH) ?: '/';
    return '/' . ltrim(rawurldecode($path), '/');
}

function header_value(string $name): ?string
{
    $key = 'HTTP_' . strtoupper(str_replace('-', '_', $name));
    foreach ([$key, 'REDIRECT_' . $key] as $k) {
        if (isset($_SERVER[$k]) && $_SERVER[$k] !== '') {
            return (string) $_SERVER[$k];
        }
    }
    if (function_exists('getallheaders')) {
        foreach (getallheaders() as $k => $v) {
            if (strcasecmp($k, $name) === 0) {
                return (string) $v;
            }
        }
    }
    return null;
}

function bearer_token(): string
{
    $auth = header_value('Authorization') ?? '';
    return str_starts_with($auth, 'Bearer ') ? substr($auth, 7) : '';
}

/** Visitor IP. Behind Hostinger's CDN the real address is the last X-Forwarded-For entry. */
function client_ip(): string
{
    $remote = $_SERVER['REMOTE_ADDR'] ?? 'unknown';
    if (tprint_config()['trust_forwarded_for']) {
        $xff = header_value('X-Forwarded-For');
        if ($xff) {
            $parts = array_values(array_filter(array_map('trim', explode(',', $xff))));
            $last = end($parts);
            if ($last && filter_var($last, FILTER_VALIDATE_IP)) {
                return $last;
            }
        }
    }
    return $remote;
}

function form_str(string $key, ?string $default = null): string
{
    $v = $_POST[$key] ?? $default;
    if ($v === null) {
        throw new HttpError(422, "Missing field: $key");
    }
    return trim((string) $v);
}

function form_int(string $key, ?int $default = null): int
{
    $raw = $_POST[$key] ?? null;
    if ($raw === null || $raw === '') {
        if ($default === null) {
            throw new HttpError(422, "Missing field: $key");
        }
        return $default;
    }
    if (!preg_match('/^-?\d+$/', (string) $raw)) {
        throw new HttpError(422, "$key must be a whole number");
    }
    return (int) $raw;
}

function form_float(string $key): float
{
    $raw = $_POST[$key] ?? null;
    if ($raw === null || !is_numeric($raw)) {
        throw new HttpError(422, "$key must be a number");
    }
    return (float) $raw;
}

/** Same truthiness FastAPI used for form booleans. */
function form_bool(string $key, bool $default = false): bool
{
    $raw = $_POST[$key] ?? null;
    if ($raw === null || $raw === '') {
        return $default;
    }
    return in_array(strtolower((string) $raw), ['1', 'true', 'on', 'yes'], true);
}

function form_optional_int(string $key): ?int
{
    $raw = $_POST[$key] ?? '';
    return $raw === '' ? null : form_int($key);
}

/**
 * Refuse state-changing requests sent from another website (CSRF). Browsers
 * always send Origin on cross-site POSTs; the agent, Claude and curl send none.
 */
function require_same_origin(): void
{
    $origin = header_value('Origin');
    if ($origin === null || $origin === '' || $origin === 'null') {
        return;
    }
    $allowed = rtrim(tprint_config()['public_origin'], '/');
    $host = parse_url($origin, PHP_URL_HOST);
    if ($origin === $allowed || in_array($host, ['localhost', '127.0.0.1'], true)) {
        return;
    }
    throw new HttpError(403, 'Cross-site request refused');
}

// ------------------------------------------------------------------ response
function send_security_headers(): void
{
    header('X-Content-Type-Options: nosniff');
    header('Referrer-Policy: same-origin');
    header('Cache-Control: no-store');
    if (str_starts_with(tprint_config()['public_origin'], 'https://')) {
        header('Strict-Transport-Security: max-age=31536000');
    }
}

function json_out($data, int $status = 200): never
{
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRESERVE_ZERO_FRACTION);
    exit;
}

function send_file(string $path, string $contentType, string $downloadName, bool $inline): never
{
    if (!is_file($path)) {
        throw new HttpError(404, 'File not found');
    }
    $safeName = preg_replace('/[^A-Za-z0-9._ -]/', '_', $downloadName) ?: 'file';
    header('Content-Type: ' . $contentType);
    header('Content-Length: ' . filesize($path));
    header(sprintf('Content-Disposition: %s; filename="%s"', $inline ? 'inline' : 'attachment', $safeName));
    readfile($path);
    exit;
}

function random_token(int $bytes = 16): string
{
    return bin2hex(random_bytes($bytes));
}

require_once __DIR__ . '/db.php';
require_once __DIR__ . '/pdf.php';
require_once __DIR__ . '/pricing.php';
require_once __DIR__ . '/core.php';
