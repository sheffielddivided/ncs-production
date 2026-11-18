<?php
// Felles kode for besøkstelleren på ncs-io.kbak.no: konfigurasjon, database og
// tellelogikk. Brukes av h.php (mottak) og s/index.php (statistikk).
//
// Personvern: ingen IP-adresse, User-Agent eller identifikator lagres. For å
// telle unike besøkende per dag brukes en SHA-256-hash av (dagens salt + IP +
// User-Agent). Saltet lages tilfeldig hver dag, og saltet og hashene slettes
// når dagen er over, så de kan ikke føres tilbake til noen. Resten lagres bare
// som summer per dag (og time).
if (!defined('IO_ENTRY')) { http_response_code(404); exit; }

function io_config(): array {
    static $cfg = null;
    if ($cfg !== null) return $cfg;
    $defaults = [
        // Nettsteder som får telle (Origin- eller Referer-verten).
        'allowed_hosts' => ['energy.kbak.no'],
        // SQLite-fila. Må ligge utenfor den offentlige mappen.
        'db_path' => dirname(__DIR__, 2) . '/ncs-io-data/io.sqlite',
        'timezone' => 'Europe/Oslo',
    ];
    $file = __DIR__ . '/config.php';
    $local = is_file($file) ? (require $file) : [];
    $cfg = array_merge($defaults, is_array($local) ? $local : []);
    date_default_timezone_set($cfg['timezone']);
    return $cfg;
}

function io_db(): PDO {
    static $db = null;
    if ($db) return $db;
    $path = io_config()['db_path'];
    $dir = dirname($path);
    if (!is_dir($dir)) {
        @mkdir($dir, 0750, true);
        // Ekstra sperre i tilfelle mappen likevel havner under en webrot.
        @file_put_contents($dir . '/.htaccess', "Require all denied\nDeny from all\n");
    }
    $db = new PDO('sqlite:' . $path, null, null, [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]);
    $db->exec('PRAGMA journal_mode=WAL; PRAGMA busy_timeout=3000; PRAGMA synchronous=NORMAL;');
    $db->exec('
        CREATE TABLE IF NOT EXISTS daily  (day TEXT PRIMARY KEY, visitors INT NOT NULL DEFAULT 0, views INT NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS views  (day TEXT, path TEXT, n INT NOT NULL DEFAULT 0, visits INT NOT NULL DEFAULT 0, PRIMARY KEY (day, path));
        CREATE TABLE IF NOT EXISTS hours  (day TEXT, hour INT, n INT NOT NULL DEFAULT 0, PRIMARY KEY (day, hour));
        CREATE TABLE IF NOT EXISTS dims   (day TEXT, dim TEXT, val TEXT, n INT NOT NULL DEFAULT 0, PRIMARY KEY (day, dim, val));
        CREATE TABLE IF NOT EXISTS events (day TEXT, name TEXT, page TEXT, n INT NOT NULL DEFAULT 0, PRIMARY KEY (day, name, page));
        CREATE TABLE IF NOT EXISTS salt   (day TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS seen   (day TEXT, h TEXT, path TEXT, PRIMARY KEY (day, h, path));
    ');
    return $db;
}

// Dagens salt. Første treff en ny dag lager et nytt og sletter gårsdagens salt
// og hasher, så besøkende ikke kan følges fra dag til dag.
function io_salt(PDO $db, string $day): string {
    $s = $db->prepare('SELECT value FROM salt WHERE day = ?');
    $s->execute([$day]);
    $v = $s->fetchColumn();
    if ($v) return $v;
    $v = bin2hex(random_bytes(32));
    $db->prepare('INSERT OR IGNORE INTO salt (day, value) VALUES (?, ?)')->execute([$day, $v]);
    $db->prepare('DELETE FROM salt WHERE day <> ?')->execute([$day]);
    $db->prepare('DELETE FROM seen WHERE day <> ?')->execute([$day]);
    $s->execute([$day]);
    return $s->fetchColumn();
}

function io_bump(PDO $db, string $sql, array $args): void {
    $db->prepare($sql)->execute($args);
}

// Grove grupper av nettleser og system; hele User-Agent lagres aldri.
function io_browser(string $ua): string {
    if (preg_match('/Edg(A|iOS)?\//', $ua)) return 'Edge';
    if (preg_match('/OPR\/|Opera/', $ua)) return 'Opera';
    if (preg_match('/SamsungBrowser/', $ua)) return 'Samsung Internet';
    if (preg_match('/Firefox\/|FxiOS/', $ua)) return 'Firefox';
    if (preg_match('/Chrome\/|CriOS/', $ua)) return 'Chrome';
    if (preg_match('/Safari\//', $ua)) return 'Safari';
    return 'Other';
}
function io_os(string $ua): string {
    if (preg_match('/iPhone|iPad|iPod/', $ua)) return 'iOS';
    if (preg_match('/Android/', $ua)) return 'Android';
    if (preg_match('/Windows/', $ua)) return 'Windows';
    if (preg_match('/CrOS/', $ua)) return 'ChromeOS';
    if (preg_match('/Mac OS X|Macintosh/', $ua)) return 'macOS';
    if (preg_match('/Linux/', $ua)) return 'Linux';
    return 'Other';
}
function io_device(int $w): string {
    if ($w <= 0) return 'Unknown';
    if ($w < 768) return 'Mobile';
    if ($w < 1100) return 'Tablet';
    return 'Desktop';
}
function io_is_bot(string $ua): bool {
    return $ua === '' || (bool)preg_match('/bot|crawl|spider|slurp|headless|lighthouse|preview|monitor|curl|wget|python|httpclient|phantom|puppeteer|playwright/i', $ua);
}

// Registrerer ett treff. $hit: ['k' => 'v'|'e', 'p' => sti, 'e' => hendelse,
// 'r' => henviser, 'w' => skjermbredde, 'l' => språk].
function io_record(array $hit, string $ip, string $ua, string $originHost): bool {
    if (io_is_bot($ua)) return false;
    $kind = ($hit['k'] ?? '') === 'e' ? 'e' : 'v';
    $path = (string)($hit['p'] ?? '');
    if (!preg_match('#^/[a-z0-9/_-]{0,100}$#', $path)) return false;
    $db = io_db();
    $day = date('Y-m-d');
    $h = hash('sha256', io_salt($db, $day) . '|' . $ip . '|' . $ua . '|' . $originHost);
    $db->beginTransaction();
    try {
        if ($kind === 'e') {
            $name = (string)($hit['e'] ?? '');
            if (!preg_match('/^[a-z0-9_-]{1,40}$/', $name)) { $db->rollBack(); return false; }
            io_bump($db, 'INSERT INTO events (day, name, page, n) VALUES (?, ?, ?, 1)
                ON CONFLICT(day, name, page) DO UPDATE SET n = n + 1', [$day, $name, $path]);
            $db->commit();
            return true;
        }
        // Ny besøkende i dag? Da telles nettleser, system, enhet og språk.
        $newVisitor = $db->prepare('INSERT OR IGNORE INTO seen (day, h, path) VALUES (?, ?, ?)');
        $newVisitor->execute([$day, $h, '']);
        $isNew = $newVisitor->rowCount() > 0;
        $newPath = $db->prepare('INSERT OR IGNORE INTO seen (day, h, path) VALUES (?, ?, ?)');
        $newPath->execute([$day, $h, $path]);
        $isNewPath = $newPath->rowCount() > 0;

        io_bump($db, 'INSERT INTO daily (day, visitors, views) VALUES (?, ?, 1)
            ON CONFLICT(day) DO UPDATE SET visitors = visitors + excluded.visitors, views = views + 1', [$day, $isNew ? 1 : 0]);
        io_bump($db, 'INSERT INTO views (day, path, n, visits) VALUES (?, ?, 1, ?)
            ON CONFLICT(day, path) DO UPDATE SET n = n + 1, visits = visits + excluded.visits', [$day, $path, $isNewPath ? 1 : 0]);
        io_bump($db, 'INSERT INTO hours (day, hour, n) VALUES (?, ?, 1)
            ON CONFLICT(day, hour) DO UPDATE SET n = n + 1', [$day, (int)date('G')]);
        $dim = function (string $d, string $v) use ($db, $day) {
            io_bump($db, 'INSERT INTO dims (day, dim, val, n) VALUES (?, ?, ?, 1)
                ON CONFLICT(day, dim, val) DO UPDATE SET n = n + 1', [$day, $d, mb_substr($v, 0, 80)]);
        };
        if ($isNew) {
            $dim('browser', io_browser($ua));
            $dim('os', io_os($ua));
            $dim('device', io_device((int)($hit['w'] ?? 0)));
            $lang = strtolower(substr(preg_replace('/[^A-Za-z-]/', '', (string)($hit['l'] ?? '')), 0, 2));
            $dim('lang', $lang !== '' ? $lang : 'unknown');
        }
        // Henviser: bare verten, og bare ved første treff i et sidebesøk.
        if (array_key_exists('r', $hit)) {
            $ref = strtolower((string)parse_url((string)$hit['r'], PHP_URL_HOST));
            $ref = preg_replace('/^www\./', '', $ref);
            if ($ref === '' ) $ref = '(direct)';
            if (!in_array($ref, io_config()['allowed_hosts'], true)) $dim('ref', $ref);
        }
        $db->commit();
        return true;
    } catch (Throwable $e) {
        if ($db->inTransaction()) $db->rollBack();
        throw $e;
    }
}
