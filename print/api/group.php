<?php
/**
 * Group (class) orders on print.tfix.in.
 *   POST ?action=create  (JSON) {title, rep_name, rep_phone, note, deadline} -> {code, admin}
 *   GET  ?action=info&code=GK7Q2M                public: title, rep first name, note, deadline, open/closed, count
 *   GET  ?action=manage&code=..&admin=..          the rep: every classmate's #CP token, name, files, total, paid, status
 *   POST ?action=close | open (JSON) {code, admin}
 * Classmates join by ordering with ?group=CODE; each pays for their own order.
 */
declare(strict_types=1);
require __DIR__ . '/lib.php';

$pdo = db();
$action = (string)($_GET['action'] ?? '');

function body_json(): array
{
    $d = json_decode((string)file_get_contents('php://input', false, null, 0, 8000), true);
    if (!is_array($d)) {
        fail(400, 'Please refresh the page and try again.');
    }
    return $d;
}

function group_by_code(string $code): ?array
{
    $code = strtoupper(preg_replace('/[^A-Za-z0-9]/', '', $code) ?? '');
    if (!preg_match('/^G[A-Z0-9]{5}$/', $code)) {
        return null;
    }
    $st = db()->prepare('SELECT * FROM print_groups WHERE code = ?');
    $st->execute([$code]);
    $g = $st->fetch();
    return $g ?: null;
}

function group_admin(array $in): array
{
    $g = group_by_code((string)($in['code'] ?? ''));
    $admin = (string)($in['admin'] ?? '');
    if (!$g || !preg_match('/^[a-f0-9]{32}$/', $admin) || !hash_equals($g['admin_hash'], hash('sha256', $admin))) {
        usleep(200000);
        fail(404, 'Group not found.');
    }
    return $g;
}

function public_group(array $g): array
{
    $st = db()->prepare("SELECT COUNT(*) FROM orders WHERE group_id = ? AND status <> 'cancelled'");
    $st->execute([(int)$g['id']]);
    return [
        'code' => $g['code'], 'title' => $g['title'], 'rep' => explode(' ', trim($g['rep_name']))[0],
        'note' => $g['note'], 'deadline' => $g['deadline'], 'status' => $g['status'], 'orders' => (int)$st->fetchColumn(),
    ];
}

switch ($action) {
    case 'create':
        require_method('POST');
        $in = body_json();
        $ip = client_ip();
        $pdo->prepare('DELETE FROM rate_events WHERE created_at < ?')->execute([gmdate('Y-m-d H:i:s', time() - 3600)]);
        $st = $pdo->prepare('SELECT COUNT(*) FROM rate_events WHERE ip = ?');
        $st->execute([$ip]);
        if ((int)$st->fetchColumn() >= ORDERS_PER_IP_PER_HOUR) {
            fail(429, 'Too many requests from this device. Please try again later.');
        }
        $title = clean_text((string)($in['title'] ?? ''), 80);
        $name = clean_text((string)($in['rep_name'] ?? ''), 60);
        $phone = normalize_phone((string)($in['rep_phone'] ?? ''));
        $note = clean_text((string)($in['note'] ?? ''), 200);
        if ($title === '') {
            fail(400, 'Give the group a name, e.g. "S6 ECE · DSP lab record".');
        }
        if ($name === '' || $phone === null) {
            fail(400, 'Enter your name and a valid 10-digit mobile number above.');
        }
        $deadline = null;
        if (!empty($in['deadline'])) {
            $d = DateTime::createFromFormat('!Y-m-d', (string)$in['deadline'], new DateTimeZone('Asia/Kolkata'));
            $today = new DateTime('today', new DateTimeZone('Asia/Kolkata'));
            if (!$d || $d < $today || $d > (clone $today)->modify('+60 days')) {
                fail(400, 'Choose a deadline within the next 60 days.');
            }
            $deadline = $d->format('Y-m-d');
        }
        $admin = bin2hex(random_bytes(16));
        $alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
        for ($try = 0; $try < 6; $try++) {
            $code = 'G';
            for ($i = 0; $i < 5; $i++) {
                $code .= $alphabet[random_int(0, strlen($alphabet) - 1)];
            }
            try {
                $pdo->prepare('INSERT INTO print_groups (code, admin_hash, title, rep_name, rep_phone, note, deadline, created_at, updated_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)')
                    ->execute([$code, hash('sha256', $admin), $title, $name, $phone, $note, $deadline, now(), now()]);
                $pdo->prepare('INSERT INTO rate_events (ip, created_at) VALUES (?, ?)')->execute([$ip, now()]);
                send_json(201, ['code' => $code, 'admin' => $admin] + public_group(group_by_code($code)));
            } catch (PDOException $e) {
                // code taken: try another
            }
        }
        fail(500, 'Could not create the group. Please try again.');

    case 'info':
        require_method('GET');
        $g = group_by_code((string)($_GET['code'] ?? ''));
        if (!$g) {
            fail(404, 'Group not found. Check the link with your class rep.');
        }
        send_json(200, public_group($g));

    case 'manage':
        require_method('GET');
        $g = group_admin(['code' => $_GET['code'] ?? '', 'admin' => $_GET['admin'] ?? '']);
        $st = $pdo->prepare('SELECT * FROM orders WHERE group_id = ? ORDER BY id');
        $st->execute([(int)$g['id']]);
        $members = [];
        foreach ($st->fetchAll() as $o) {
            $files = order_files((int)$o['id']);
            $ok = array_filter($files, fn($f) => $f['error'] === null);
            $members[] = [
                'code' => $o['code'], 'name' => $o['name'], 'phone' => masked_phone($o['phone']), 'status' => $o['status'],
                'files' => count($files), 'pages' => array_sum(array_map(fn($f) => (int)($f['pages'] ?? $f['est_pages'] ?? 0), $files)),
                'total' => $o['confirmed_total'] !== null ? (float)$o['confirmed_total'] : ($o['est_total'] !== null ? (float)$o['est_total'] : null),
                'paid' => $ok && count(array_filter($ok, fn($f) => (int)$f['paid'] === 1)) === count($ok),
                'at' => iso($o['created_at']),
            ];
        }
        send_json(200, public_group($g) + ['members' => $members]);

    case 'close':
    case 'open':
        require_method('POST');
        $g = group_admin(body_json());
        $pdo->prepare('UPDATE print_groups SET status = ?, updated_at = ? WHERE id = ?')
            ->execute([$action === 'close' ? 'closed' : 'open', now(), (int)$g['id']]);
        send_json(200, public_group(group_by_code($g['code'])));

    default:
        fail(404, 'Unknown action.');
}
