<?php
/**
 * Claude connector at https://print.tfix.in/mcp: a stateless MCP server over
 * Streamable HTTP (JSON responses, no SSE), written for shared PHP hosting.
 *
 * Same tools and MCP App dashboard (ui://tprint/dashboard.html) as the Python
 * connector. Auth: fixed bearer token (config 'mcp_token'). Printing through
 * Claude also needs config 'mcp_allow_print' => true, and still goes through
 * the same print gate as the admin page.
 */
declare(strict_types=1);

const MCP_PROTOCOLS = ['2025-11-25', '2025-06-18', '2025-03-26'];
const DASHBOARD_URI = 'ui://tprint/dashboard.html';
const APP_MIME = 'text/html;profile=mcp-app';
const MCP_STATUSES = ['queued', 'printing', 'ready', 'collected', 'all'];
// Customer phone numbers and file names never go to Claude.
const MCP_JOB_FIELDS = ['id', 'status', 'paid', 'cost', 'pages', 'sheets', 'copies', 'color', 'doubleside',
                        'pages_per_sheet', 'paper_size', 'files_deleted', 'created_at'];

final class ToolError extends RuntimeException {}

function mcp_handle(): never
{
    $token = tprint_config()['mcp_token'];
    if (strlen($token) < 32) {
        json_out(['error' => 'The Claude connector is turned off on this server.'], 404);
    }
    if (!hash_equals($token, bearer_token())) {
        json_out(['error' => 'Unauthorized'], 401);
    }
    if (request_method() !== 'POST') {
        header('Allow: POST');
        json_out(['error' => 'Method not allowed'], 405);
    }
    require_same_origin();
    $version = header_value('MCP-Protocol-Version');
    if ($version !== null && !in_array($version, MCP_PROTOCOLS, true)) {
        json_out(['error' => 'Unsupported MCP-Protocol-Version'], 400);
    }
    $msg = json_decode((string) file_get_contents('php://input'), true);
    if (!is_array($msg) || array_is_list($msg) || ($msg['jsonrpc'] ?? '') !== '2.0' || !isset($msg['method'])) {
        json_out(['jsonrpc' => '2.0', 'id' => null, 'error' => ['code' => -32600, 'message' => 'Invalid request']], 400);
    }
    if (!array_key_exists('id', $msg)) {           // notification
        http_response_code(202);
        exit;
    }
    $id = $msg['id'];
    $params = is_array($msg['params'] ?? null) ? $msg['params'] : [];
    try {
        $result = mcp_dispatch((string) $msg['method'], $params);
    } catch (McpRpcError $e) {
        json_out(['jsonrpc' => '2.0', 'id' => $id, 'error' => ['code' => $e->getCode(), 'message' => $e->getMessage()]]);
    }
    json_out(['jsonrpc' => '2.0', 'id' => $id, 'result' => $result]);
}

final class McpRpcError extends RuntimeException {}

function mcp_dispatch(string $method, array $params)
{
    switch ($method) {
        case 'initialize':
            $asked = (string) ($params['protocolVersion'] ?? '');
            return [
                'protocolVersion' => in_array($asked, MCP_PROTOCOLS, true) ? $asked : MCP_PROTOCOLS[0],
                'capabilities' => ['tools' => ['listChanged' => false], 'resources' => ['listChanged' => false, 'subscribe' => false]],
                'serverInfo' => ['name' => 'T-PRINT', 'title' => 'T-PRINT · T FIX AI Print', 'version' => TPRINT_VERSION],
                'instructions' => 'T-PRINT manages a print shop queue. Use open_print_dashboard to show the interactive queue. '
                    . 'Never claim a job is paid: only shop staff can confirm payment. Before calling print_paid_job, '
                    . 'get explicit confirmation of the job ID and printer from the user.',
            ];
        case 'ping':
            return new stdClass();
        case 'tools/list':
            return ['tools' => mcp_tools()];
        case 'tools/call':
            return mcp_call_tool((string) ($params['name'] ?? ''), is_array($params['arguments'] ?? null) ? $params['arguments'] : []);
        case 'resources/list':
            return ['resources' => [[
                'uri' => DASHBOARD_URI, 'name' => 'tprint-dashboard', 'title' => 'T-PRINT dashboard',
                'description' => 'Interactive print queue for the shop owner.', 'mimeType' => APP_MIME,
                '_meta' => ['ui' => ['prefersBorder' => true]],
            ]]];
        case 'resources/read':
            if (($params['uri'] ?? '') !== DASHBOARD_URI) {
                throw new McpRpcError('Resource not found', -32002);
            }
            return ['contents' => [[
                'uri' => DASHBOARD_URI, 'mimeType' => APP_MIME,
                'text' => (string) file_get_contents(__DIR__ . '/mcp_dashboard.html'),
                '_meta' => ['ui' => ['prefersBorder' => true]],
            ]]];
        case 'resources/templates/list':
            return ['resourceTemplates' => []];
        case 'prompts/list':
            return ['prompts' => []];
        default:
            throw new McpRpcError("Method not found: $method", -32601);
    }
}

function mcp_tools(): array
{
    $read = ['readOnlyHint' => true, 'destructiveHint' => false, 'openWorldHint' => false];
    $query = ['type' => 'object', 'properties' => [
        'status' => ['type' => 'string', 'enum' => MCP_STATUSES, 'default' => 'queued'],
        'limit' => ['type' => 'integer', 'minimum' => 1, 'maximum' => 200, 'default' => 50],
    ]];
    $none = ['type' => 'object', 'properties' => new stdClass()];
    return [
        [
            'name' => 'open_print_dashboard', 'title' => 'Open T-PRINT dashboard',
            'description' => 'Show the live T-PRINT print-shop dashboard: job queue, payment state, printer status and pricing. '
                . 'Staff can refresh it and send paid jobs to a printer from inside the dashboard. '
                . 'status: queued (default), printing, ready, collected or all. limit: 1..200.',
            'inputSchema' => $query, 'annotations' => $read, '_meta' => ['ui' => ['resourceUri' => DASHBOARD_URI]],
        ],
        ['name' => 'list_print_jobs', 'description' => 'Read up to 200 jobs; status may be queued, printing, ready, collected or all.',
         'inputSchema' => $query, 'annotations' => $read],
        ['name' => 'list_printers', 'description' => "Read the shop's Windows printers and their reported status.",
         'inputSchema' => $none, 'annotations' => $read],
        ['name' => 'get_pricing', 'description' => 'Read current printing prices in INR.', 'inputSchema' => $none, 'annotations' => $read],
        ['name' => 'get_auto_print_settings', 'description' => 'Read auto-print rules. Auto-print requires staff-confirmed payment.',
         'inputSchema' => $none, 'annotations' => $read],
        [
            'name' => 'print_paid_job',
            'description' => "Physically print one paid, queued job. Obtain the user's explicit confirmation of job ID and printer "
                . 'before setting confirm=true. Cannot confirm payments or reprint.',
            'inputSchema' => ['type' => 'object', 'required' => ['job_id', 'printer_name'], 'properties' => [
                'job_id' => ['type' => 'integer'], 'printer_name' => ['type' => 'string'],
                'confirm' => ['type' => 'boolean', 'default' => false],
            ]],
            'annotations' => ['readOnlyHint' => false, 'destructiveHint' => true, 'idempotentHint' => true, 'openWorldHint' => true],
        ],
    ];
}

function mcp_call_tool(string $name, array $args): array
{
    try {
        switch ($name) {
            case 'open_print_dashboard':
                $snap = dashboard_snapshot((string) ($args['status'] ?? 'queued'), (int) ($args['limit'] ?? 50));
                return ['content' => [['type' => 'text', 'text' => snapshot_text($snap)]], 'structuredContent' => $snap];
            case 'list_print_jobs':
                [$status, $limit] = mcp_check_query((string) ($args['status'] ?? 'queued'), (int) ($args['limit'] ?? 50));
                $jobs = $status === 'all'
                    ? rows('SELECT * FROM jobs ORDER BY created_at DESC LIMIT ' . $limit)
                    : rows('SELECT * FROM jobs WHERE status=? ORDER BY created_at DESC LIMIT ' . $limit, [$status]);
                return mcp_text(array_map('mcp_job', $jobs));
            case 'list_printers':
                return mcp_text(mcp_printers());
            case 'get_pricing':
                return mcp_text(array_map(fn($p) => array_map(fn($v) => is_numeric($v) ? $v + 0 : $v, $p), rows('SELECT * FROM pricing ORDER BY paper_size, color')));
            case 'get_auto_print_settings':
                return mcp_text(row('SELECT enabled, printer_name, bw_only, max_sheets, max_copies FROM auto_print_settings WHERE id=1'));
            case 'print_paid_job':
                if (!tprint_config()['mcp_allow_print']) {
                    throw new ToolError('MCP printing disabled. Shop owner must enable it in tprint-config.php.');
                }
                if (($args['confirm'] ?? false) !== true) {
                    throw new ToolError('Explicit confirmation required.');
                }
                try {
                    $job = dispatch_job((int) ($args['job_id'] ?? 0), (string) ($args['printer_name'] ?? ''), 'claude_mcp');
                } catch (PrintBlocked $e) {
                    throw new ToolError($e->getMessage());
                }
                return mcp_text(['job' => mcp_job($job), 'result' => 'sent to the shop PC for printing; physical completion is not verified']);
            default:
                throw new ToolError("Unknown tool: $name");
        }
    } catch (ToolError $e) {
        return ['content' => [['type' => 'text', 'text' => $e->getMessage()]], 'isError' => true];
    }
}

function mcp_text($data): array
{
    return ['content' => [['type' => 'text', 'text' => json_encode($data, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_PRESERVE_ZERO_FRACTION)]]];
}

function mcp_check_query(string $status, int $limit): array
{
    if (!in_array($status, MCP_STATUSES, true)) {
        throw new ToolError('status must be one of: ' . implode(', ', MCP_STATUSES));
    }
    if ($limit < 1 || $limit > 200) {
        throw new ToolError('limit must be 1..200');
    }
    return [$status, $limit];
}

function mcp_job(array $job): array
{
    return array_intersect_key(job_types($job), array_flip(MCP_JOB_FIELDS));
}

function mcp_printers(): array
{
    return array_map(fn($p) => [
        'id' => (int) $p['id'], 'name' => $p['name'], 'windows_printer_name' => $p['windows_printer_name'],
        'enabled' => (int) $p['enabled'], 'status' => printer_live_status($p), 'shop_pc' => $p['agent_name'],
    ], rows('SELECT * FROM printers ORDER BY name'));
}

function dashboard_snapshot(string $status, int $limit): array
{
    [$status, $limit] = mcp_check_query($status, $limit);
    $all = rows('SELECT * FROM jobs ORDER BY created_at DESC LIMIT 2000');
    $counts = ['queued' => 0, 'printing' => 0, 'ready' => 0, 'collected' => 0];
    $awaiting = 0;
    foreach ($all as $j) {
        $counts[$j['status']] = ($counts[$j['status']] ?? 0) + 1;
        if ($j['status'] === 'queued' && !(int) $j['paid']) {
            $awaiting++;
        }
    }
    $counts['awaiting_payment'] = $awaiting;
    $matching = $status === 'all' ? $all : array_values(array_filter($all, fn($j) => $j['status'] === $status));
    $auto = row('SELECT enabled, printer_name, bw_only, max_sheets, max_copies FROM auto_print_settings WHERE id=1');
    return [
        'status_filter' => $status,
        'generated_at' => (new DateTimeImmutable('now', new DateTimeZone(tprint_config()['timezone'])))->format('Y-m-d\TH:i:sP'),
        'printing_enabled' => (bool) tprint_config()['mcp_allow_print'],
        'admin_url' => rtrim(tprint_config()['public_origin'], '/') . '/admin/',
        'shop_pc_online' => agent_status()['online'],
        'counts' => $counts,
        'jobs' => array_map('mcp_job', array_slice($matching, 0, $limit)),
        'total_matching' => count($matching),
        'printers' => mcp_printers(),
        'pricing' => array_map(fn($p) => ['paper_size' => $p['paper_size'], 'color' => (int) $p['color'],
            'singleside_cost' => (float) $p['singleside_cost'], 'doubleside_cost' => (float) $p['doubleside_cost'],
            'min_cost' => (float) $p['min_cost']], rows('SELECT * FROM pricing ORDER BY paper_size, color')),
        'auto_print' => array_map(fn($v) => is_numeric($v) ? (int) $v : $v, $auto ?: []),
    ];
}

function snapshot_text(array $s): string
{
    $c = $s['counts'];
    $lines = [
        "T-PRINT queue - showing {$s['status_filter']} (" . count($s['jobs']) . " of {$s['total_matching']}).",
        "Totals: {$c['queued']} queued ({$c['awaiting_payment']} awaiting payment), {$c['printing']} printing, "
            . "{$c['ready']} ready, {$c['collected']} collected.",
        'Shop PC print agent: ' . ($s['shop_pc_online'] ? 'online' : 'OFFLINE') . '. Remote printing from Claude: '
            . ($s['printing_enabled'] ? 'ENABLED' : 'disabled') . '.',
        'Printers: ' . ($s['printers'] ? implode(', ', array_map(fn($p) => "{$p['windows_printer_name']} ({$p['status']}"
            . ($p['enabled'] ? '' : ', disabled') . ')', $s['printers'])) : 'none registered') . '.',
        "Full admin (payments, prices, settings): {$s['admin_url']}",
    ];
    foreach ($s['jobs'] as $j) {
        $lines[] = sprintf('- Job %d: %s, %s, Rs %s, %d sheet(s) x %d cop%s, %s %s%s%s', $j['id'], $j['status'],
            $j['paid'] ? 'PAID' : 'UNPAID', rtrim(rtrim(number_format($j['cost'], 2, '.', ''), '0'), '.'), $j['sheets'],
            $j['copies'], $j['copies'] === 1 ? 'y' : 'ies', $j['color'] ? 'colour' : 'B&W', $j['paper_size'],
            $j['doubleside'] ? ', double-sided' : '', $j['files_deleted'] ? ', FILES DELETED' : '');
    }
    return implode("\n", $lines);
}
