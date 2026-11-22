<?php
/*
 * T-PRINT private settings. Copy this file to
 *     /home/<your-user>/domains/tfix.in/tprint-config.php
 * i.e. NEXT TO public_html, never inside it, and fill in every value.
 * Generate the random values with: php -r "echo bin2hex(random_bytes(24)), PHP_EOL;"
 * (or any password manager's 48-character generator).
 */
return [
    // hPanel > Databases > MySQL Databases
    'db' => [
        'host' => '127.0.0.1',
        'port' => 3306,
        'name' => 'u000000000_tprint',
        'user' => 'u000000000_tprint',
        'pass' => 'CHANGE-ME',
    ],

    'public_origin' => 'https://print.tfix.in',

    // Uploaded customer files. Outside public_html so they are never directly downloadable.
    'storage_dir' => __DIR__ . '/tprint-storage',

    // First-time admin setup: the admin page asks for this code once, then you choose a password.
    'setup_code' => 'CHANGE-ME-48-random-characters',
    'admin_username' => 'admin',

    // The print agent on the shop PC sends this key (TPRINT_AGENT_KEY on the PC).
    'agent_key' => 'CHANGE-ME-48-random-characters',

    // Claude connector at https://print.tfix.in/mcp. Leave '' to turn the connector off.
    'mcp_token' => '',
    // Let Claude (and the dashboard inside Claude) send paid jobs to the printer.
    'mcp_allow_print' => false,

    'timezone' => 'Asia/Kolkata',
];
