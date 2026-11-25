<?php
/**
 * GET /api/status.php?code=RP-105&t=TOKEN — a customer checks their repair
 * (and the CLUTCH Rewards of the mobile number on it). Needs the private token.
 */
declare(strict_types=1);
require __DIR__ . '/../../print/api/lib.php';

require_method('GET');
$code = strtoupper(preg_replace('/[^A-Za-z0-9-]/', '', (string)($_GET['code'] ?? '')) ?? '');
$token = (string)($_GET['t'] ?? '');
if (!preg_match('/^RP-\d{3,9}$/', $code) || !preg_match('/^[a-f0-9]{32}$/', $token)) {
    fail(404, 'Repair not found.');
}
$st = db()->prepare('SELECT * FROM repairs WHERE code = ?');
$st->execute([$code]);
$r = $st->fetch();
if (!$r || !hash_equals($r['token_hash'], hash('sha256', $token))) {
    fail(404, 'Repair not found.');
}
$out = public_repair($r);
$out['rewards'] = rewards_for($r['phone']) + [
    'order_reward' => repair_reward((int)$r['id']),
    'history' => array_map(fn($e) => array_diff_key($e, ['note' => 1]), ledger_for($r['phone'], 10)),
];
send_json(200, $out);
