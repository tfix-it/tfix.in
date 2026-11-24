<?php
/**
 * CLUTCHPRINT customer account — mobile number = username, 4-digit PIN = password.
 *
 *   POST ?action=login        phone, pin          -> {session, phone (masked), name}
 *   GET  ?action=orders       header X-Client-Session -> {phone, name, orders:[...]}
 *   POST ?action=change_pin   pin, new_pin (+ session) -> {ok}
 *   POST ?action=logout       (+ session)          -> {ok}
 *
 * Accounts are created by the first online order from a mobile number
 * (order.php). Forgotten PINs are reset by staff from T Print admin.
 */
declare(strict_types=1);
require __DIR__ . '/lib.php';

$action = (string)($_GET['action'] ?? '');

function signed_in(): string
{
    $phone = session_phone();
    if ($phone === null) {
        fail(401, 'Please sign in with your mobile number and PIN.');
    }
    return $phone;
}

switch ($action) {
    case 'login':
        require_method('POST');
        $phone = normalize_phone((string)($_POST['phone'] ?? ''));
        if ($phone === null) {
            fail(400, 'Please enter a valid 10-digit mobile number.');
        }
        if (find_customer($phone) === null) {
            usleep(400000);
            fail(404, 'No CLUTCHPRINT orders for this mobile number yet. Your first order creates your PIN.');
        }
        require_pin($phone, (string)($_POST['pin'] ?? ''));
        $c = find_customer($phone);
        send_json(200, ['session' => create_session($phone), 'phone' => masked_phone($phone), 'name' => $c['name']]);

    case 'orders':
        require_method('GET');
        $phone = signed_in();
        $c = find_customer($phone);
        $st = db()->prepare('SELECT * FROM orders WHERE phone = ? ORDER BY id DESC LIMIT 30');
        $st->execute([$phone]);
        $orders = [];
        foreach ($st->fetchAll() as $o) {
            $orders[] = public_order($o, order_files((int)$o['id']));
        }
        send_json(200, ['phone' => masked_phone($phone), 'name' => $c['name'] ?? '', 'orders' => $orders]);

    case 'change_pin':
        require_method('POST');
        $phone = signed_in();
        require_pin($phone, (string)($_POST['pin'] ?? ''));
        $new = (string)($_POST['new_pin'] ?? '');
        if (!valid_pin($new)) {
            fail(400, 'The new PIN must be 4 digits.');
        }
        db()->prepare('UPDATE customers SET pin_hash = ?, updated_at = ? WHERE phone = ?')
            ->execute([password_hash($new, PASSWORD_DEFAULT), now(), $phone]);
        send_json(200, ['ok' => true]);

    case 'logout':
        require_method('POST');
        $token = (string)($_SERVER['HTTP_X_CLIENT_SESSION'] ?? '');
        if ($token !== '') {
            db()->prepare('DELETE FROM client_sessions WHERE token_hash = ?')->execute([hash('sha256', $token)]);
        }
        send_json(200, ['ok' => true]);

    default:
        fail(404, 'Unknown action.');
}
