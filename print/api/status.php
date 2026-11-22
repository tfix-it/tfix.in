<?php
/**
 * GET /api/status.php?code=TPXXXXXX&t=TOKEN — a customer checks their order.
 * Both the code and the secret token from the order confirmation are needed,
 * so order codes can't be guessed to look up other people's orders.
 */
declare(strict_types=1);
require __DIR__ . '/lib.php';

require_method('GET');

$code = strtoupper(preg_replace('/[^A-Za-z0-9]/', '', (string)($_GET['code'] ?? '')) ?? '');
$token = (string)($_GET['t'] ?? '');
if (!preg_match('/^TP[A-Z0-9]{6}$/', $code) || !preg_match('/^[a-f0-9]{32}$/', $token)) {
    fail(404, 'Order not found.');
}

$st = db()->prepare('SELECT * FROM orders WHERE code = ?');
$st->execute([$code]);
$order = $st->fetch();
if (!$order || !hash_equals($order['token_hash'], hash('sha256', $token))) {
    fail(404, 'Order not found.');
}

send_json(200, public_order($order, order_files((int)$order['id'])));
