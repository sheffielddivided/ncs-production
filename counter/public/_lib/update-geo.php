<?php
// Bygger geo.sqlite (land og nettverkseier per IP-område) fra DB-IPs gratis
// databaser «IP to Country Lite» og «IP to ASN Lite» (CC BY 4.0,
// https://db-ip.com), som oppdateres månedlig. Kjøres som planlagt oppgave i
// Plesk (Scheduled Tasks → Run a PHP script), f.eks. den 3. hver måned:
//   php ncs-io.kbak.no/_lib/update-geo.php
// Henter denne månedens filer, ellers forrige måneds. Den nye databasen bygges
// ved siden av og byttes inn først når den er ferdig, så telleren aldri ser en
// halvferdig fil. Kan bare kjøres fra kommandolinjen.
if (PHP_SAPI !== 'cli') { http_response_code(404); exit; }
define('IO_ENTRY', 1);
require __DIR__ . '/io.php';

$dir = dirname(io_config()['db_path']);
if (!is_dir($dir) && !@mkdir($dir, 0750, true)) { fwrite(STDERR, "Cannot create $dir\n"); exit(1); }
@set_time_limit(0);
ini_set('memory_limit', '256M');

function geo_download(string $kind, string $dir): string {
    foreach ([0, 1] as $back) {
        $ym = date('Y-m', strtotime("first day of -$back month"));
        $url = "https://download.db-ip.com/free/dbip-$kind-lite-$ym.csv.gz";
        $dest = "$dir/dbip-$kind.csv.gz";
        $ctx = stream_context_create(['http' => ['timeout' => 120, 'user_agent' => 'ncs-io-geo-update']]);
        $in = @fopen($url, 'rb', false, $ctx);
        if (!$in) { echo "  $url: not available\n"; continue; }
        $out = fopen("$dest.part", 'wb');
        stream_copy_to_stream($in, $out);
        fclose($in); fclose($out);
        if (filesize("$dest.part") < 1000000) { @unlink("$dest.part"); echo "  $url: too small, skipped\n"; continue; }
        rename("$dest.part", $dest);
        echo "  $url: " . round(filesize($dest) / 1e6, 1) . " MB\n";
        return $dest;
    }
    throw new RuntimeException("Could not download the DB-IP $kind database");
}

// IPv4 som heltall, IPv6 som 16 byte (sorteres riktig som BLOB i SQLite).
function geo_key(string $ip) {
    if (strpos($ip, ':') === false) { $n = ip2long($ip); return $n === false ? null : $n; }
    $b = @inet_pton($ip);
    return $b === false ? null : $b;
}

try {
    echo "Downloading DB-IP Lite databases…\n";
    $cFile = geo_download('country', $dir);
    $aFile = geo_download('asn', $dir);
    $tmp = "$dir/geo.sqlite.new";
    @unlink($tmp);
    $db = new PDO('sqlite:' . $tmp, null, null, [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]);
    $db->exec('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
        CREATE TABLE c4 (s INTEGER, e INTEGER, cc TEXT);
        CREATE TABLE c6 (s BLOB, e BLOB, cc TEXT);
        CREATE TABLE a4 (s INTEGER, e INTEGER, org INTEGER);
        CREATE TABLE a6 (s BLOB, e BLOB, org INTEGER);
        CREATE TABLE orgs (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);');
    $db->beginTransaction();
    $ins = ['c4' => $db->prepare('INSERT INTO c4 VALUES (?, ?, ?)'), 'c6' => $db->prepare('INSERT INTO c6 VALUES (?, ?, ?)')];
    $n = 0;
    $gz = gzopen($cFile, 'rb');
    while (($row = fgetcsv($gz, 0, ',', '"', '')) !== false) {
        if (count($row) < 3) continue;
        [$s, $e] = [geo_key($row[0]), geo_key($row[1])];
        if ($s === null || $e === null) continue;
        $t = is_int($s) ? 'c4' : 'c6';
        $st = $ins[$t];
        $st->bindValue(1, $s, is_int($s) ? PDO::PARAM_INT : PDO::PARAM_LOB);
        $st->bindValue(2, $e, is_int($e) ? PDO::PARAM_INT : PDO::PARAM_LOB);
        $st->bindValue(3, $row[2]);
        $st->execute();
        $n++;
    }
    gzclose($gz);
    echo "  countries: $n ranges\n";
    $ins = ['a4' => $db->prepare('INSERT INTO a4 VALUES (?, ?, ?)'), 'a6' => $db->prepare('INSERT INTO a6 VALUES (?, ?, ?)')];
    $orgIns = $db->prepare('INSERT INTO orgs (id, name) VALUES (?, ?)');
    $orgs = [];
    $n = 0;
    $gz = gzopen($aFile, 'rb');
    while (($row = fgetcsv($gz, 0, ',', '"', '')) !== false) {
        if (count($row) < 4) continue;
        [$s, $e] = [geo_key($row[0]), geo_key($row[1])];
        if ($s === null || $e === null) continue;
        $name = trim($row[3]) !== '' ? trim($row[3]) : 'AS' . $row[2];
        if (!isset($orgs[$name])) { $orgs[$name] = count($orgs) + 1; $orgIns->execute([$orgs[$name], $name]); }
        $t = is_int($s) ? 'a4' : 'a6';
        $st = $ins[$t];
        $st->bindValue(1, $s, is_int($s) ? PDO::PARAM_INT : PDO::PARAM_LOB);
        $st->bindValue(2, $e, is_int($e) ? PDO::PARAM_INT : PDO::PARAM_LOB);
        $st->bindValue(3, $orgs[$name], PDO::PARAM_INT);
        $st->execute();
        $n++;
    }
    gzclose($gz);
    echo "  networks: $n ranges, " . count($orgs) . " organisations\n";
    $db->prepare('INSERT INTO meta VALUES (?, ?)')->execute(['built', date('c')]);
    $db->commit();
    $db->exec('CREATE INDEX c4s ON c4 (s); CREATE INDEX c6s ON c6 (s); CREATE INDEX a4s ON a4 (s); CREATE INDEX a6s ON a6 (s);');
    $db = null;
    rename($tmp, "$dir/geo.sqlite");
    @unlink($cFile); @unlink($aFile);
    echo "Done: $dir/geo.sqlite (" . round(filesize("$dir/geo.sqlite") / 1e6, 1) . " MB)\n";
} catch (Throwable $e) {
    fwrite(STDERR, 'Geo update failed: ' . $e->getMessage() . "\n");
    exit(1);
}
