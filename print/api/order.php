<?php
/**
 * POST /api/order.php — a customer places an order (works while the shop is closed).
 *
 * multipart/form-data:
 *   name, phone, notes, est_total
 *   files[]   — up to MAX_FILES_PER_ORDER files (pdf, jpg, png, docx, pptx, ppt)
 *   options   — JSON array, one object per file, same order as files[]:
 *               {color, doubleside, duplex_edge, paper_size, orientation,
 *                pages_per_sheet, copies, pages_range, est_pages}
 *
 * Returns {code, token}. The token is needed (with the code) to check status;
 * only a hash of it is stored.
 */
declare(strict_types=1);
require __DIR__ . '/lib.php';

require_method('POST');

// PHP silently drops the whole body when it is larger than post_max_size.
if (empty($_POST) && empty($_FILES) && (int)($_SERVER['CONTENT_LENGTH'] ?? 0) > 0) {
    fail(413, 'Files are too large. Please send fewer or smaller files.');
}

$pdo = db();

// --- rate limit per IP ---
$ip = client_ip();
$pdo->prepare('DELETE FROM rate_events WHERE created_at < ?')->execute([gmdate('Y-m-d H:i:s', time() - 3600)]);
$st = $pdo->prepare('SELECT COUNT(*) FROM rate_events WHERE ip = ?');
$st->execute([$ip]);
if ((int)$st->fetchColumn() >= ORDERS_PER_IP_PER_HOUR) {
    fail(429, 'Too many orders from this device. Please try again later or WhatsApp us.');
}

// --- customer details ---
$name = clean_text((string)($_POST['name'] ?? ''), 60);
$phone = normalize_phone((string)($_POST['phone'] ?? ''));
$notes = clean_text((string)($_POST['notes'] ?? ''), 400);
if ($name === '') {
    fail(400, 'Please enter your name.');
}
if ($phone === null) {
    fail(400, 'Please enter a valid 10-digit mobile number.');
}
$estTotal = is_numeric($_POST['est_total'] ?? null) ? max(0, min(1000000, (float)$_POST['est_total'])) : null;

// --- files + options ---
$files = $_FILES['files'] ?? null;
if (!$files || !is_array($files['name'] ?? null) || count($files['name']) === 0) {
    fail(400, 'Please add at least one file.');
}
$count = count($files['name']);
if ($count > MAX_FILES_PER_ORDER) {
    fail(400, 'You can send up to ' . MAX_FILES_PER_ORDER . ' files in one order.');
}
$options = json_decode((string)($_POST['options'] ?? ''), true);
if (!is_array($options) || count($options) !== $count) {
    fail(400, 'Print options are missing. Please refresh the page and try again.');
}

$items = [];
$totalBytes = 0;
for ($i = 0; $i < $count; $i++) {
    $orig = clean_text(basename((string)$files['name'][$i]), 200);
    $err = (int)$files['error'][$i];
    if ($err === UPLOAD_ERR_INI_SIZE || $err === UPLOAD_ERR_FORM_SIZE) {
        fail(413, "“{$orig}” is too large (max " . (MAX_FILE_BYTES / 1048576) . ' MB).');
    }
    if ($err !== UPLOAD_ERR_OK || !is_uploaded_file($files['tmp_name'][$i])) {
        fail(400, "“{$orig}” did not upload completely. Please try again.");
    }
    $size = (int)$files['size'][$i];
    if ($size <= 0 || $size > MAX_FILE_BYTES) {
        fail(413, "“{$orig}” is too large (max " . (MAX_FILE_BYTES / 1048576) . ' MB).');
    }
    $totalBytes += $size;
    $ext = strtolower(pathinfo($orig, PATHINFO_EXTENSION));
    if (!in_array($ext, ALLOWED_EXT, true) || !sniff_ok($files['tmp_name'][$i], $ext)) {
        fail(400, "“{$orig}” is not a supported file. Use PDF, JPG, PNG, DOCX or PPTX.");
    }

    $o = is_array($options[$i]) ? $options[$i] : [];
    $pps = (int)($o['pages_per_sheet'] ?? 1);
    $copies = (int)($o['copies'] ?? 1);
    $range = str_replace(' ', '', (string)($o['pages_range'] ?? ''));
    $paper = (string)($o['paper_size'] ?? 'A4');
    $edge = (string)($o['duplex_edge'] ?? 'long');
    $orient = (string)($o['orientation'] ?? 'portrait');
    if (!in_array($pps, ALLOWED_PPS, true) || $copies < 1 || $copies > 500
        || !in_array($paper, ['A4', 'A3'], true) || !in_array($edge, ['long', 'short'], true)
        || !in_array($orient, ['portrait', 'landscape'], true) || !valid_pages_range($range)) {
        fail(400, "Print options for “{$orig}” are not valid.");
    }
    $estPages = isset($o['est_pages']) && is_numeric($o['est_pages']) ? max(0, min(100000, (int)$o['est_pages'])) : null;

    $items[] = [
        'tmp' => $files['tmp_name'][$i], 'orig' => $orig, 'ext' => $ext, 'size' => $size,
        'color' => !empty($o['color']) ? 1 : 0, 'doubleside' => !empty($o['doubleside']) ? 1 : 0,
        'duplex_edge' => $edge, 'paper_size' => $paper, 'orientation' => $orient,
        'pps' => $pps, 'copies' => $copies, 'range' => $range, 'est_pages' => $estPages,
    ];
}
if ($totalBytes > MAX_ORDER_BYTES) {
    fail(413, 'The files are too large together (max ' . (MAX_ORDER_BYTES / 1048576) . ' MB per order).');
}

// --- store ---
$uploads = data_dir() . '/uploads/';
$moved = [];
$token = bin2hex(random_bytes(16));
try {
    $pdo->beginTransaction();
    $now = now();
    $code = null;
    for ($try = 0; $try < 5 && $code === null; $try++) {
        $candidate = random_code();
        $chk = $pdo->prepare('SELECT 1 FROM orders WHERE code = ?');
        $chk->execute([$candidate]);
        if (!$chk->fetchColumn()) {
            $code = $candidate;
        }
    }
    if ($code === null) {
        throw new RuntimeException('code');
    }
    $pdo->prepare('INSERT INTO orders (code, token_hash, phone, name, notes, status, est_total, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, \'pending\', ?, ?, ?)')
        ->execute([$code, hash('sha256', $token), $phone, $name, $notes, $estTotal, $now, $now]);
    $orderId = (int)$pdo->lastInsertId();
    // Pickup token shown to the customer and staff: #CP-104, #CP-105, ...
    // (short enough to say at the counter; status lookups still need the secret token)
    $code = 'CP-' . ($orderId + 100);
    $pdo->prepare('UPDATE orders SET code = ? WHERE id = ?')->execute([$code, $orderId]);

    $ins = $pdo->prepare('INSERT INTO order_files (order_id, original_name, ext, stored_name, size_bytes, color, doubleside,
        duplex_edge, paper_size, orientation, pages_per_sheet, copies, pages_range, est_pages)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)');
    foreach ($items as $it) {
        $stored = bin2hex(random_bytes(16));   // no extension: never executable, never guessable
        if (!move_uploaded_file($it['tmp'], $uploads . $stored)) {
            throw new RuntimeException('move');
        }
        @chmod($uploads . $stored, 0600);
        $moved[] = $uploads . $stored;
        $ins->execute([$orderId, $it['orig'], $it['ext'], $stored, $it['size'], $it['color'], $it['doubleside'],
            $it['duplex_edge'], $it['paper_size'], $it['orientation'], $it['pps'], $it['copies'], $it['range'], $it['est_pages']]);
    }
    $pdo->prepare('INSERT INTO rate_events (ip, created_at) VALUES (?, ?)')->execute([$ip, $now]);
    $pdo->commit();
} catch (Throwable $e) {
    if ($pdo->inTransaction()) {
        $pdo->rollBack();
    }
    foreach ($moved as $m) {
        @unlink($m);
    }
    error_log('[tprint order] ' . $e->getMessage());
    fail(500, 'Could not save your order. Please try again.');
}

send_json(201, ['code' => $code, 'token' => $token, 'status' => 'pending']);
