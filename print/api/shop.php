<?php
/**
 * Shop API — used only by the T Print server on the shop PC.
 * Auth: header "X-TPrint-Token: <shop token>" (only its SHA-256 is stored in config).
 *
 *   GET  ?action=ping                 health check + upload limits
 *   GET  ?action=pending              orders waiting to be fetched, with file options
 *   GET  ?action=file&id=N            download one uploaded file
 *   POST ?action=ack     (JSON)       {order_id, files:[{file_id, job_id, pages, cost, error}]}
 *                                     marks the order received and deletes the files here
 *   POST ?action=status  (JSON)       {orders:[{order_id, status, files:[{file_id, job_status, paid, cost}]}]}
 *                                     a "collected" order credits CLUTCH Rewards (₹5 per ₹100)
 *   GET  ?action=rewards&phone=N      CLUTCH Rewards balance + recent history for a mobile number
 *   POST ?action=redeem  (JSON)       {phone, amount, note} use rewards at the counter (staff)
 */
declare(strict_types=1);
require __DIR__ . '/lib.php';

$provided = (string)($_SERVER['HTTP_X_TPRINT_TOKEN'] ?? '');
$expected = (string)(cfg()['shop_token_sha256'] ?? '');
if ($expected === '' || strlen($provided) < 32 || !hash_equals($expected, hash('sha256', $provided))) {
    usleep(300000);
    fail(401, 'Unauthorized');
}

// Heartbeat: the shop server calls this every minute, so the last call time
// tells customers (and staff) whether T Print on Shop PC 1 is running.
@file_put_contents(data_dir() . '/heartbeat', (string)time(), LOCK_EX);

$pdo = db();
$action = (string)($_GET['action'] ?? '');

function json_body(): array
{
    $data = json_decode((string)file_get_contents('php://input'), true);
    if (!is_array($data)) {
        fail(400, 'Expected a JSON body.');
    }
    return $data;
}

switch ($action) {
    case 'ping':
        require_method('GET');
        send_json(200, [
            'ok' => true,
            'time' => gmdate('c'),
            'upload_max_filesize' => ini_get('upload_max_filesize'),
            'post_max_size' => ini_get('post_max_size'),
            'max_file_uploads' => ini_get('max_file_uploads'),
        ]);

    case 'pending':
        require_method('GET');
        $orders = $pdo->query("SELECT * FROM orders WHERE status = 'pending' ORDER BY id LIMIT 20")->fetchAll();
        $out = [];
        foreach ($orders as $o) {
            $files = [];
            foreach (order_files((int)$o['id']) as $f) {
                $files[] = [
                    'file_id' => (int)$f['id'], 'original_name' => $f['original_name'], 'ext' => $f['ext'],
                    'size_bytes' => (int)$f['size_bytes'], 'color' => (bool)$f['color'],
                    'doubleside' => (bool)$f['doubleside'], 'duplex_edge' => $f['duplex_edge'],
                    'paper_size' => $f['paper_size'], 'orientation' => $f['orientation'],
                    'pages_per_sheet' => (int)$f['pages_per_sheet'], 'copies' => (int)$f['copies'],
                    'pages_range' => $f['pages_range'], 'available' => (int)$f['file_deleted'] === 0,
                ];
            }
            $out[] = [
                'order_id' => (int)$o['id'], 'code' => $o['code'], 'phone' => $o['phone'], 'name' => $o['name'],
                'notes' => $o['notes'], 'created_at' => iso($o['created_at']), 'files' => $files,
            ];
        }
        send_json(200, ['orders' => $out]);

    case 'file':
        require_method('GET');
        $st = $pdo->prepare('SELECT * FROM order_files WHERE id = ?');
        $st->execute([(int)($_GET['id'] ?? 0)]);
        $f = $st->fetch();
        $path = $f ? data_dir() . '/uploads/' . basename($f['stored_name']) : '';
        if (!$f || (int)$f['file_deleted'] === 1 || !is_file($path)) {
            fail(404, 'File not available.');
        }
        header('Content-Type: application/octet-stream');
        header('Content-Length: ' . filesize($path));
        header('Cache-Control: no-store');
        header('X-Content-Type-Options: nosniff');
        readfile($path);
        exit;

    case 'ack':
        require_method('POST');
        $body = json_body();
        $orderId = (int)($body['order_id'] ?? 0);
        $st = $pdo->prepare('SELECT * FROM orders WHERE id = ?');
        $st->execute([$orderId]);
        $order = $st->fetch();
        if (!$order) {
            fail(404, 'Order not found.');
        }
        $byId = [];
        foreach (order_files($orderId) as $f) {
            $byId[(int)$f['id']] = $f;
        }
        $total = 0.0;
        $upd = $pdo->prepare('UPDATE order_files SET job_id = ?, pages = ?, cost = ?, error = ?, job_status = ? WHERE id = ? AND order_id = ?');
        foreach ((array)($body['files'] ?? []) as $r) {
            $fid = (int)($r['file_id'] ?? 0);
            if (!isset($byId[$fid])) {
                continue;
            }
            $error = isset($r['error']) && $r['error'] !== null ? clean_text((string)$r['error'], 200) : null;
            $cost = isset($r['cost']) && is_numeric($r['cost']) ? round((float)$r['cost'], 2) : null;
            $upd->execute([
                isset($r['job_id']) ? (int)$r['job_id'] : null,
                isset($r['pages']) ? (int)$r['pages'] : null,
                $cost, $error, $error === null ? 'queued' : 'failed', $fid, $orderId,
            ]);
            $total += $cost ?? 0;
            if ($error === null) {
                delete_stored_file($byId[$fid]);   // the shop has it now
            }
        }
        $pdo->prepare("UPDATE orders SET status = CASE WHEN status = 'pending' THEN 'received' ELSE status END,
                       confirmed_total = ?, fetched_at = COALESCE(fetched_at, ?), updated_at = ? WHERE id = ?")
            ->execute([round($total, 2), now(), now(), $orderId]);
        send_json(200, ['ok' => true]);

    case 'status':
        require_method('POST');
        $body = json_body();
        $setOrder = $pdo->prepare('UPDATE orders SET status = ?, updated_at = ? WHERE id = ?');
        $setFile = $pdo->prepare('UPDATE order_files SET job_status = ?, paid = ? WHERE id = ? AND order_id = ?');
        $setCost = $pdo->prepare('UPDATE order_files SET cost = ? WHERE id = ? AND order_id = ?');
        $setTotal = $pdo->prepare('UPDATE orders SET confirmed_total = (SELECT ROUND(COALESCE(SUM(cost), 0), 2) FROM order_files
                                   WHERE order_id = ? AND error IS NULL) WHERE id = ?');
        $n = 0;
        foreach ((array)($body['orders'] ?? []) as $o) {
            $oid = (int)($o['order_id'] ?? 0);
            $status = (string)($o['status'] ?? '');
            if ($oid <= 0 || !in_array($status, ORDER_STATUSES, true)) {
                continue;
            }
            $setOrder->execute([$status, now(), $oid]);
            $costs = false;
            foreach ((array)($o['files'] ?? []) as $f) {
                $fid = (int)($f['file_id'] ?? 0);
                $js = (string)($f['job_status'] ?? '');
                if (in_array($js, JOB_STATUSES, true)) {
                    $setFile->execute([$js, !empty($f['paid']) ? 1 : 0, $fid, $oid]);
                }
                // Final price from T Print (staff may have corrected it after the order came in).
                if (isset($f['cost']) && is_numeric($f['cost']) && $f['cost'] >= 0 && $f['cost'] <= 1000000) {
                    $setCost->execute([round((float)$f['cost'], 2), $fid, $oid]);
                    $costs = true;
                }
            }
            if ($costs) {
                $setTotal->execute([$oid, $oid]);
            }
            if ($status === 'collected') {
                credit_order($oid);
            }
            $n++;
        }
        send_json(200, ['ok' => true, 'updated' => $n]);

    case 'rewards':
        require_method('GET');
        $phone = normalize_phone((string)($_GET['phone'] ?? ''));
        if ($phone === null) {
            fail(400, 'Not a valid 10-digit mobile number.');
        }
        $st = $pdo->prepare('SELECT c.order_id, c.amount, c.reward, c.created_at, o.code, o.name FROM reward_credits c
                             LEFT JOIN orders o ON o.id = c.order_id WHERE c.phone = ? ORDER BY c.created_at DESC LIMIT 10');
        $st->execute([$phone]);
        $credits = array_map(fn($r) => [
            'code' => $r['code'], 'name' => $r['name'], 'amount' => (float)$r['amount'],
            'reward' => (float)$r['reward'], 'at' => iso($r['created_at']),
        ], $st->fetchAll());
        $st = $pdo->prepare('SELECT amount, note, created_at FROM reward_redemptions WHERE phone = ? ORDER BY id DESC LIMIT 10');
        $st->execute([$phone]);
        $used = array_map(fn($r) => ['amount' => (float)$r['amount'], 'note' => $r['note'], 'at' => iso($r['created_at'])], $st->fetchAll());
        $st = $pdo->prepare('SELECT name FROM orders WHERE phone = ? ORDER BY id DESC LIMIT 1');
        $st->execute([$phone]);
        $r = rewards_for($phone);
        $r['phone'] = $phone;   // staff see the full number
        send_json(200, $r + ['name' => (string)($st->fetchColumn() ?: ''), 'credits' => $credits, 'redemptions' => $used]);

    case 'redeem':
        // Staff use a customer's rewards at the counter (the bill is reduced by this amount).
        require_method('POST');
        $body = json_body();
        $phone = normalize_phone((string)($body['phone'] ?? ''));
        $amount = $body['amount'] ?? null;
        if ($phone === null) {
            fail(400, 'Not a valid 10-digit mobile number.');
        }
        if (!is_numeric($amount) || (float)$amount <= 0 || round((float)$amount, 2) != (float)$amount) {
            fail(400, 'Enter an amount in rupees, e.g. 5 or 10.');
        }
        $amount = round((float)$amount, 2);
        $note = clean_text((string)($body['note'] ?? ''), 100);
        $pdo->beginTransaction();
        $balance = rewards_for($phone)['balance'];
        if ($amount > $balance + 0.001) {
            $pdo->rollBack();
            fail(409, 'Only ₹' . $balance . ' of rewards available for this number.');
        }
        $pdo->prepare('INSERT INTO reward_redemptions (phone, amount, note, created_at) VALUES (?, ?, ?, ?)')
            ->execute([$phone, $amount, $note, now()]);
        $pdo->commit();
        send_json(200, ['ok' => true, 'phone' => $phone, 'used' => $amount, 'balance' => rewards_for($phone)['balance']]);

    default:
        fail(404, 'Unknown action.');
}
