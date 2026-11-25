<?php
/** GET /api/stats.php — public green counter: sheets of paper saved by CLUTCHPRINT customers. */
declare(strict_types=1);
require __DIR__ . '/lib.php';

require_method('GET');
send_json(200, green_stats());
