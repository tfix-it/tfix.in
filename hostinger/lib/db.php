<?php
/**
 * MySQL access and schema. The schema is created/extended automatically on
 * the first request after an upload, so deploying is just copying files.
 */
declare(strict_types=1);

const SCHEMA_VERSION = 1;

function db(): PDO
{
    static $pdo = null;
    if ($pdo !== null) {
        return $pdo;
    }
    $c = tprint_config()['db'] ?? null;
    if (!$c) {
        throw new HttpError(500, 'Database settings are missing from tprint-config.php.');
    }
    $dsn = sprintf('mysql:host=%s;port=%d;dbname=%s;charset=utf8mb4',
        $c['host'] ?? '127.0.0.1', (int) ($c['port'] ?? 3306), $c['name']);
    $pdo = new PDO($dsn, $c['user'], $c['pass'], [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        PDO::ATTR_EMULATE_PREPARES => false,
        PDO::ATTR_STRINGIFY_FETCHES => false,
    ]);
    $pdo->exec("SET time_zone = '+00:00'");
    migrate($pdo);
    return $pdo;
}

function q(string $sql, array $params = []): PDOStatement
{
    $stmt = db()->prepare($sql);
    $stmt->execute($params);
    return $stmt;
}

function row(string $sql, array $params = []): ?array
{
    $r = q($sql, $params)->fetch();
    return $r === false ? null : $r;
}

function rows(string $sql, array $params = []): array
{
    return q($sql, $params)->fetchAll();
}

function in_transaction(callable $fn)
{
    $pdo = db();
    $pdo->beginTransaction();
    try {
        $result = $fn($pdo);
        $pdo->commit();
        return $result;
    } catch (Throwable $e) {
        if ($pdo->inTransaction()) {
            $pdo->rollBack();
        }
        throw $e;
    }
}

function migrate(PDO $pdo): void
{
    $pdo->exec('CREATE TABLE IF NOT EXISTS schema_info (id TINYINT PRIMARY KEY, version INT NOT NULL) ENGINE=InnoDB');
    $current = (int) ($pdo->query('SELECT version FROM schema_info WHERE id=1')->fetchColumn() ?: 0);
    if ($current >= SCHEMA_VERSION) {
        return;
    }
    $T = 'ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci';
    $statements = [
        "CREATE TABLE IF NOT EXISTS uploads (
            id CHAR(32) PRIMARY KEY,
            original_filename VARCHAR(255) NOT NULL,
            file_type VARCHAR(10) NOT NULL,
            source_file VARCHAR(64) NULL,
            pdf_file VARCHAR(64) NULL,
            pages INT NULL,
            status VARCHAR(16) NOT NULL,
            error VARCHAR(255) NULL,
            ip VARCHAR(64) NOT NULL,
            created_at DATETIME(6) NOT NULL,
            updated_at DATETIME(6) NOT NULL,
            KEY uploads_status (status, created_at)
        ) $T",
        "CREATE TABLE IF NOT EXISTS jobs (
            id INT AUTO_INCREMENT PRIMARY KEY,
            token CHAR(32) NOT NULL UNIQUE,
            phone VARCHAR(20) NOT NULL,
            original_filename VARCHAR(255) NOT NULL,
            upload_id CHAR(32) NOT NULL,
            pdf_file VARCHAR(64) NOT NULL,
            source_file VARCHAR(64) NULL,
            file_type VARCHAR(10) NOT NULL,
            pages INT NOT NULL,
            pages_range VARCHAR(500) NOT NULL DEFAULT '',
            sheets INT NOT NULL,
            copies INT NOT NULL,
            color TINYINT NOT NULL,
            doubleside TINYINT NOT NULL,
            duplex_edge VARCHAR(8) NOT NULL DEFAULT 'long',
            pages_per_sheet INT NOT NULL,
            paper_size VARCHAR(8) NOT NULL,
            orientation VARCHAR(10) NOT NULL DEFAULT 'portrait',
            scale_mode VARCHAR(8) NOT NULL DEFAULT 'fit',
            auto_printed TINYINT NOT NULL DEFAULT 0,
            daily_seq INT NOT NULL DEFAULT 1,
            files_deleted TINYINT NOT NULL DEFAULT 0,
            cost DECIMAL(10,2) NOT NULL,
            status VARCHAR(12) NOT NULL DEFAULT 'queued',
            paid TINYINT NOT NULL DEFAULT 0,
            created_at DATETIME(6) NOT NULL,
            updated_at DATETIME(6) NOT NULL,
            KEY jobs_phone_day (phone, created_at),
            KEY jobs_status (status, created_at),
            KEY jobs_updated (updated_at)
        ) $T",
        "CREATE TABLE IF NOT EXISTS printers (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            windows_printer_name VARCHAR(255) NOT NULL UNIQUE,
            enabled TINYINT NOT NULL DEFAULT 1,
            live_status VARCHAR(16) NOT NULL DEFAULT 'unknown',
            last_reported_at DATETIME(6) NULL
        ) $T",
        "CREATE TABLE IF NOT EXISTS pricing (
            id INT AUTO_INCREMENT PRIMARY KEY,
            paper_size VARCHAR(8) NOT NULL,
            color TINYINT NOT NULL,
            singleside_cost DECIMAL(10,2) NOT NULL,
            doubleside_cost DECIMAL(10,2) NOT NULL,
            min_cost DECIMAL(10,2) NOT NULL,
            UNIQUE KEY pricing_size_color (paper_size, color)
        ) $T",
        "CREATE TABLE IF NOT EXISTS auto_print_settings (
            id TINYINT PRIMARY KEY,
            enabled TINYINT NOT NULL DEFAULT 0,
            printer_name VARCHAR(255) NULL,
            bw_only TINYINT NOT NULL DEFAULT 1,
            max_sheets INT NULL,
            max_copies INT NULL
        ) $T",
        "CREATE TABLE IF NOT EXISTS app_settings (
            id TINYINT PRIMARY KEY,
            max_upload_mb INT NOT NULL DEFAULT 50,
            unpaid_cleanup_days INT NOT NULL DEFAULT 3,
            rate_limit_window_sec INT NOT NULL DEFAULT 600,
            rate_limit_max_requests INT NOT NULL DEFAULT 10,
            upi_id VARCHAR(100) NOT NULL DEFAULT '',
            upi_payee_name VARCHAR(100) NOT NULL DEFAULT '',
            agent_last_seen DATETIME(6) NULL,
            agent_version VARCHAR(40) NULL,
            last_cleanup_at DATETIME(6) NULL
        ) $T",
        "CREATE TABLE IF NOT EXISTS admin_credentials (
            id TINYINT PRIMARY KEY,
            password_hash VARCHAR(255) NOT NULL,
            updated_at DATETIME(6) NOT NULL
        ) $T",
        "CREATE TABLE IF NOT EXISTS admin_sessions (
            token_hash CHAR(64) PRIMARY KEY,
            created_at DATETIME(6) NOT NULL,
            expires_at DATETIME(6) NOT NULL
        ) $T",
        "CREATE TABLE IF NOT EXISTS print_dispatches (
            job_id INT PRIMARY KEY,
            printer VARCHAR(255) NOT NULL,
            actor VARCHAR(32) NOT NULL,
            state VARCHAR(16) NOT NULL,
            error VARCHAR(255) NULL,
            created_at DATETIME(6) NOT NULL,
            updated_at DATETIME(6) NOT NULL,
            KEY dispatch_state (state)
        ) $T",
        "CREATE TABLE IF NOT EXISTS payment_audit (
            id INT AUTO_INCREMENT PRIMARY KEY,
            job_id INT NOT NULL,
            paid TINYINT NOT NULL,
            actor VARCHAR(32) NOT NULL,
            ip VARCHAR(64) NULL,
            created_at DATETIME(6) NOT NULL,
            KEY audit_job (job_id)
        ) $T",
        "CREATE TABLE IF NOT EXISTS rate_hits (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            bucket VARCHAR(16) NOT NULL,
            ip VARCHAR(64) NOT NULL,
            created_at DATETIME(6) NOT NULL,
            KEY rate_lookup (bucket, ip, created_at)
        ) $T",
    ];
    foreach ($statements as $sql) {
        $pdo->exec($sql);
    }
    // Seed defaults (same values the Python server shipped with).
    $seed = $pdo->prepare('INSERT IGNORE INTO pricing (paper_size, color, singleside_cost, doubleside_cost, min_cost) VALUES (?,?,?,?,?)');
    foreach ([['A4', 0, 2, 3, 10], ['A4', 1, 10, 15, 10], ['A3', 0, 5, 7, 15], ['A3', 1, 15, 20, 15]] as $p) {
        $seed->execute($p);
    }
    $pdo->exec('INSERT IGNORE INTO auto_print_settings (id) VALUES (1)');
    $pdo->exec('INSERT IGNORE INTO app_settings (id) VALUES (1)');
    $pdo->exec('INSERT INTO schema_info (id, version) VALUES (1, ' . SCHEMA_VERSION . ') ON DUPLICATE KEY UPDATE version=VALUES(version)');
}
