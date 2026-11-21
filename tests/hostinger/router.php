<?php
// Router for `php -S` in tests: emulates hostinger/.htaccess.
$path = parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH);
if (preg_match('#^/lib(/|$)#', $path)) { http_response_code(403); exit; }
if (str_starts_with($path, '/api/') || preg_match('#^/mcp/?$#', $path)) {
    require getenv('TPRINT_DOCROOT') . '/api/index.php';
    return true;
}
return false;   // static files (index.html, admin/, assets/)
