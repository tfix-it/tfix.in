<?php
// Copy to config.php on the server (never commit config.php).
// Generate the shop token on any PC:  python -c "import secrets; print(secrets.token_urlsafe(48))"
// Put the token itself in T Print (PRINTSHOP_CLOUD_TOKEN) and only its SHA-256 here:
//   python -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" YOUR_TOKEN
return [
    'db_dsn'  => 'mysql:host=127.0.0.1;port=3306;dbname=u000000000_tprint;charset=utf8mb4',
    'db_user' => 'u000000000_tprint',
    'db_pass' => 'CHANGE_ME',
    'shop_token_sha256' => 'CHANGE_ME',
    // Optional. Default: <domain folder>/tprint-data (outside public_html).
    // 'data_dir' => '/home/u000000000/domains/example.com/tprint-data',
];
