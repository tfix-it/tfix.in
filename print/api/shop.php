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
 *   GET  ?action=ledger_verify        check the tamper-evident rewards log (hash chain)
 *   POST ?action=cancel  (JSON)       {order_ids:[...]} or {all_open:true} cancel unfinished orders (staff)
 *   GET  ?action=repairs[&all=1]      repair bookings from repair.tfix.in (open ones by default)
 *   POST ?action=repair_update (JSON) {repair_id, status, quote, final_amount, paid, note}; collected+paid -> rewards
 *   GET  ?action=backup               full copy of every table (JSON) for the owner's backups
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
// tells customers (and staff) whether T Print on Shop PC 1 is running. A backup download is not a heartbeat.
$action = (string)($_GET['action'] ?? '');
if ($action !== 'backup') @file_put_contents(data_dir() . '/heartbeat', (string)time(), LOCK_EX);

$pdo = db();

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
            $group = null;
            if (!empty($o['group_id'])) {
                $gs = $pdo->prepare('SELECT code, title FROM print_groups WHERE id = ?');
                $gs->execute([(int)$o['group_id']]);
                $group = $gs->fetch() ?: null;
            }
            $out[] = [
                'order_id' => (int)$o['id'], 'code' => $o['code'], 'phone' => $o['phone'], 'name' => $o['name'],
                'notes' => $o['notes'], 'created_at' => iso($o['created_at']), 'files' => $files, 'group' => $group,
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
        // A cancelled order stays cancelled (staff cancelled it on the website).
        $setOrder = $pdo->prepare("UPDATE orders SET status = ?, updated_at = ? WHERE id = ? AND status <> 'cancelled'");
        $cur = $pdo->prepare('SELECT status FROM orders WHERE id = ?');
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
            $cur->execute([$oid]);
            if (in_array($cur->fetchColumn(), [false, 'cancelled'], true)) {
                continue;   // unknown or cancelled order: ignore the update
            }
            $setOrder->execute([$status, now(), $oid]);
            if ($status === 'ready') {
                notify_ready($oid);   // ready alert to the customer's phone (only phones not yet told)
            }
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
                @unlink(data_dir() . '/green-stats.json');   // green counter refreshes
            }
            $n++;
        }
        send_json(200, ['ok' => true, 'updated' => $n]);

    case 'cancel':
        // Staff: cancel orders that are not finished. {order_ids:[...]} or {all_open:true}.
        // Files still waiting here are deleted. Collected and already-cancelled orders are left alone.
        require_method('POST');
        $body = json_body();
        $open = "status NOT IN ('collected', 'cancelled')";
        if (!empty($body['all_open'])) {
            $ids = array_map('intval', $pdo->query("SELECT id FROM orders WHERE $open")->fetchAll(PDO::FETCH_COLUMN));
        } else {
            $ids = array_values(array_filter(array_map('intval', (array)($body['order_ids'] ?? [])), fn($i) => $i > 0));
        }
        $done = [];
        $set = $pdo->prepare("UPDATE orders SET status = 'cancelled', updated_at = ? WHERE id = ? AND $open");
        $get = $pdo->prepare('SELECT code FROM orders WHERE id = ?');
        $cur = $pdo->prepare('SELECT status FROM orders WHERE id = ?');
        foreach ($ids as $oid) {
            $cur->execute([$oid]);
            $was = $cur->fetchColumn();
            if ($was !== false && !in_array($was, ['collected', 'cancelled'], true)) {
                $set->execute([now(), $oid]);
                foreach (order_files($oid) as $f) {
                    delete_stored_file($f);
                }
                $get->execute([$oid]);
                $done[] = (string)$get->fetchColumn();
            }
        }
        send_json(200, ['ok' => true, 'cancelled' => $done]);

    case 'repairs':
        // Staff: repair bookings from repair.tfix.in. ?all=1 includes finished ones (latest 100).
        require_method('GET');
        $sql = !empty($_GET['all'])
            ? 'SELECT * FROM repairs ORDER BY id DESC LIMIT 100'
            : "SELECT * FROM repairs WHERE status NOT IN ('collected', 'cancelled') ORDER BY id LIMIT 200";
        $out = [];
        foreach ($pdo->query($sql)->fetchAll() as $r) {
            $diag = $r['diagnosis'] !== null ? json_decode((string)$r['diagnosis'], true) : null;
            $out[] = ['repair_id' => (int)$r['id'], 'phone' => $r['phone'], 'diagnosis' => is_array($diag) ? $diag : null,
                      'reward' => repair_reward((int)$r['id'])] + public_repair($r);
        }
        send_json(200, ['repairs' => $out]);

    case 'repair_update':
        // Staff: {repair_id, status?, quote?, final_amount?, paid?, note?}. Collected + paid credits rewards.
        require_method('POST');
        $body = json_body();
        $rid = (int)($body['repair_id'] ?? 0);
        $st = $pdo->prepare('SELECT * FROM repairs WHERE id = ?');
        $st->execute([$rid]);
        $r = $st->fetch();
        if (!$r) {
            fail(404, 'Repair not found.');
        }
        $money = function ($v) {
            if ($v === null || $v === '') {
                return null;
            }
            if (!is_numeric($v) || (float)$v < 0 || (float)$v > 1000000) {
                fail(400, 'Enter amounts in rupees.');
            }
            return round((float)$v, 2);
        };
        $status = array_key_exists('status', $body) ? (string)$body['status'] : $r['status'];
        if (!in_array($status, REPAIR_STATUSES, true)) {
            fail(400, 'Unknown repair status.');
        }
        if ($r['status'] === 'collected' && $status !== 'collected') {
            fail(409, 'This repair is already collected.');
        }
        $quote = array_key_exists('quote', $body) ? $money($body['quote']) : ($r['quote'] !== null ? (float)$r['quote'] : null);
        $final = array_key_exists('final_amount', $body) ? $money($body['final_amount']) : ($r['final_amount'] !== null ? (float)$r['final_amount'] : null);
        $paid = array_key_exists('paid', $body) ? (!empty($body['paid']) ? 1 : 0) : (int)$r['paid'];
        $note = array_key_exists('note', $body) ? clean_text((string)$body['note'], 300) : $r['staff_note'];
        if ($status === 'collected' && ($final === null || !$paid)) {
            fail(400, 'Enter the final amount and mark it paid before collected.');
        }
        $pdo->prepare('UPDATE repairs SET status = ?, quote = ?, final_amount = ?, paid = ?, staff_note = ?, updated_at = ? WHERE id = ?')
            ->execute([$status, $quote, $final, $paid, $note, now(), $rid]);
        if ($status === 'collected') {
            credit_repair($rid);
        }
        $st->execute([$rid]);
        $r = $st->fetch();
        send_json(200, ['ok' => true, 'repair_id' => $rid, 'phone' => $r['phone'], 'reward' => repair_reward($rid)] + public_repair($r));

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
        send_json(200, $r + ['name' => (string)($st->fetchColumn() ?: ''), 'credits' => $credits, 'redemptions' => $used,
                             'history' => ledger_for($phone, 30), 'ledger' => ledger_verify()]);

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
        $entry = ledger_append($phone, 'redeem', null, $amount, -$amount, $note);
        $pdo->commit();
        send_json(200, ['ok' => true, 'phone' => $phone, 'used' => $amount, 'balance' => rewards_for($phone)['balance'],
                        'receipt' => substr($entry['hash'], 0, 12)]);

    case 'ledger_verify':
        require_method('GET');
        send_json(200, ledger_verify());

    case 'backup':
        require_method('GET');
        $tables = ['orders', 'order_files', 'reward_credits', 'reward_redemptions', 'reward_ledger', 'repairs',
                   'repair_credits', 'print_groups', 'push_subs', 'rate_events'];
        $out = ['generated' => gmdate('c'), 'driver' => $pdo->getAttribute(PDO::ATTR_DRIVER_NAME), 'tables' => [], 'ledger' => ledger_verify()];
        foreach ($tables as $t) {
            $out['tables'][$t] = $pdo->query("SELECT * FROM $t ORDER BY 1")->fetchAll(PDO::FETCH_ASSOC);
        }
        header('Content-Disposition: attachment; filename="clutchprint-backup-' . gmdate('Ymd-His') . '.json"');
        send_json(200, $out);

    default:
        fail(404, 'Unknown action.');
}
