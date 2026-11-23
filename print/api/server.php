<?php
/**
 * GET /api/server.php — is the T Print shop server (Shop PC 1) running?
 * It contacts the website every minute (see shop.php heartbeat), so it counts
 * as online when the last contact was within ONLINE_WITHIN_SEC.
 */
declare(strict_types=1);
require __DIR__ . '/lib.php';

const ONLINE_WITHIN_SEC = 180;

require_method('GET');
$file = data_dir() . '/heartbeat';
$last = is_file($file) ? (int)trim((string)@file_get_contents($file)) : 0;
$ago = $last > 0 ? max(0, time() - $last) : null;

send_json(200, [
    'online' => $ago !== null && $ago <= ONLINE_WITHIN_SEC,
    'last_seen' => $last > 0 ? gmdate('Y-m-d\TH:i:s\Z', $last) : null,
    'seconds_ago' => $ago,
]);
