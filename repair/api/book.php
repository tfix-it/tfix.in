<?php
/**
 * POST /api/book.php — book a repair on repair.tfix.in (works while the shop is closed).
 * JSON body: {name, phone, device_type, brand, model, issues:[...], description, visit_date, diagnosis:{...}}
 * Mobile number is mandatory: it links the repair to the customer's CLUTCH Rewards (shared with print.tfix.in).
 * Returns {code, token}: code = repair token (RP-105); token = secret for the private status link.
 */
declare(strict_types=1);
require __DIR__ . '/../../print/api/lib.php';

require_method('POST');
$raw = (string)file_get_contents('php://input', false, null, 0, 20000);
$in = json_decode($raw, true);
if (!is_array($in)) {
    fail(400, 'Please refresh the page and try again.');
}
$pdo = db();

// shared hourly limit per device/network (same table as print orders)
$ip = client_ip();
$pdo->prepare('DELETE FROM rate_events WHERE created_at < ?')->execute([gmdate('Y-m-d H:i:s', time() - 3600)]);
$st = $pdo->prepare('SELECT COUNT(*) FROM rate_events WHERE ip = ?');
$st->execute([$ip]);
if ((int)$st->fetchColumn() >= ORDERS_PER_IP_PER_HOUR) {
    fail(429, 'Too many bookings from this device. Please try again later or WhatsApp us.');
}

$name = clean_text((string)($in['name'] ?? ''), 60);
$phone = normalize_phone((string)($in['phone'] ?? ''));
if ($name === '') {
    fail(400, 'Please enter your name.');
}
if ($phone === null) {
    fail(400, 'Please enter a valid 10-digit mobile number.');
}
$device = (string)($in['device_type'] ?? '');
if (!in_array($device, REPAIR_DEVICES, true)) {
    fail(400, 'Please choose the device type.');
}
$brand = clean_text((string)($in['brand'] ?? ''), 40);
$model = clean_text((string)($in['model'] ?? ''), 60);
$issues = array_values(array_unique(array_filter((array)($in['issues'] ?? []), fn($i) => is_string($i) && in_array($i, REPAIR_ISSUES, true))));
$desc = clean_text((string)($in['description'] ?? ''), 600);
if (!$issues && $desc === '') {
    fail(400, 'Tell us what is wrong: pick a problem or write a short note.');
}
$visit = null;
if (!empty($in['visit_date'])) {
    $d = DateTime::createFromFormat('!Y-m-d', (string)$in['visit_date'], new DateTimeZone('Asia/Kolkata'));
    $today = new DateTime('today', new DateTimeZone('Asia/Kolkata'));
    if (!$d || $d < $today || $d > (clone $today)->modify('+30 days')) {
        fail(400, 'Choose a visit date within the next 30 days.');
    }
    $visit = $d->format('Y-m-d');
}

// Phone Doctor report: only known test keys and short values are kept.
$diag = null;
if (isset($in['diagnosis']) && is_array($in['diagnosis'])) {
    $tests = [];
    foreach ((array)($in['diagnosis']['tests'] ?? []) as $k => $t) {
        if (!is_string($k) || !preg_match('/^[a-z_]{2,20}$/', $k) || !is_array($t) || count($tests) >= 20) {
            continue;
        }
        $res = (string)($t['result'] ?? '');
        if (!in_array($res, ['pass', 'fail', 'skip'], true)) {
            continue;
        }
        $tests[$k] = ['result' => $res, 'detail' => clean_text((string)($t['detail'] ?? ''), 120)];
    }
    $info = [];
    foreach ((array)($in['diagnosis']['device'] ?? []) as $k => $v) {
        if (is_string($k) && preg_match('/^[a-z_]{2,20}$/', $k) && (is_string($v) || is_numeric($v)) && count($info) < 12) {
            $info[$k] = clean_text((string)$v, 80);
        }
    }
    if ($tests) {
        $diag = json_encode(['tests' => $tests, 'device' => $info, 'at' => gmdate('c')], JSON_UNESCAPED_UNICODE);
    }
}

$token = bin2hex(random_bytes(16));
try {
    $pdo->beginTransaction();
    $now = now();
    $pdo->prepare('INSERT INTO repairs (code, token_hash, phone, name, device_type, brand, model, issues, description, visit_date, diagnosis, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)')
        ->execute([random_code(), hash('sha256', $token), $phone, $name, $device, $brand, $model, implode(',', $issues), $desc, $visit, $diag, $now, $now]);
    $id = (int)$pdo->lastInsertId();
    $code = 'RP-' . ($id + 100);
    $pdo->prepare('UPDATE repairs SET code = ? WHERE id = ?')->execute([$code, $id]);
    $pdo->prepare('INSERT INTO rate_events (ip, created_at) VALUES (?, ?)')->execute([$ip, $now]);
    $pdo->commit();
} catch (Throwable $e) {
    if ($pdo->inTransaction()) {
        $pdo->rollBack();
    }
    error_log('[repair book] ' . $e->getMessage());
    fail(500, 'Could not save your booking. Please try again.');
}
send_json(201, ['code' => $code, 'token' => $token, 'status' => 'requested']);
