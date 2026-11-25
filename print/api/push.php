<?php
/**
 * Ready alerts (web push, no app).
 *   GET  ?action=key                          the site's public VAPID key
 *   POST ?action=subscribe (JSON) {code, token, endpoint}   alert this phone when the order is ready
 *   POST ?action=which     (JSON) {endpoint}  the service worker asks which order the alert is about
 * Pushes carry no data; the service worker asks "which" and shows the token.
 */
declare(strict_types=1);
require __DIR__ . '/lib.php';

$action = (string)($_GET['action'] ?? '');
$in = [];
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $in = json_decode((string)file_get_contents('php://input', false, null, 0, 4000), true);
    if (!is_array($in)) {
        fail(400, 'Bad request.');
    }
}

switch ($action) {
    case 'key':
        require_method('GET');
        send_json(200, ['key' => vapid_keys()['public']]);

    case 'subscribe':
        require_method('POST');
        $code = strtoupper(preg_replace('/[^A-Za-z0-9-]/', '', (string)($in['code'] ?? '')) ?? '');
        $token = (string)($in['token'] ?? '');
        $endpoint = (string)($in['endpoint'] ?? '');
        if (!preg_match('/^(CP-\d{3,9}|TP[A-Z0-9]{6})$/', $code) || !preg_match('/^[a-f0-9]{32}$/', $token)) {
            fail(404, 'Order not found.');
        }
        $st = db()->prepare('SELECT * FROM orders WHERE code = ?');
        $st->execute([$code]);
        $o = $st->fetch();
        if (!$o || !hash_equals($o['token_hash'], hash('sha256', $token))) {
            fail(404, 'Order not found.');
        }
        if (!push_endpoint_ok($endpoint)) {
            fail(400, 'This browser\'s alert service is not supported.');
        }
        $pdo = db();
        $n = $pdo->prepare('SELECT COUNT(*) FROM push_subs WHERE order_id = ?');
        $n->execute([(int)$o['id']]);
        if ((int)$n->fetchColumn() >= 5) {
            fail(429, 'Alerts are already on for this order on 5 devices.');
        }
        try {
            $pdo->prepare('INSERT INTO push_subs (order_id, endpoint_hash, endpoint, created_at) VALUES (?, ?, ?, ?)')
                ->execute([(int)$o['id'], hash('sha256', $endpoint), $endpoint, now()]);
        } catch (PDOException $e) {
            // already subscribed on this phone
        }
        // Already ready? Alert right away.
        if ($o['status'] === 'ready') {
            notify_ready((int)$o['id']);
        }
        send_json(200, ['ok' => true, 'status' => $o['status']]);

    case 'which':
        require_method('POST');
        $endpoint = (string)($in['endpoint'] ?? '');
        if ($endpoint === '' || strlen($endpoint) > 700) {
            send_json(200, ['orders' => []]);
        }
        $st = db()->prepare('SELECT o.code, o.status FROM push_subs p JOIN orders o ON o.id = p.order_id
                             WHERE p.endpoint_hash = ? ORDER BY p.notified_at DESC, o.id DESC LIMIT 5');
        $st->execute([hash('sha256', $endpoint)]);
        send_json(200, ['orders' => $st->fetchAll()]);

    default:
        fail(404, 'Unknown action.');
}
