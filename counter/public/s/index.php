<?php
// Statistikksiden for besøkstelleren. Mappen s/ skal være passordbeskyttet i
// Plesk (Websites & Domains → Password-Protected Directories → /s).
define('IO_ENTRY', 1);
require dirname(__DIR__) . '/_lib/io.php';
io_config();
header('Cache-Control: no-store');
header('X-Robots-Tag: noindex, nofollow');

$ranges = ['7' => 'Last 7 days', '30' => 'Last 30 days', '90' => 'Last 90 days', '365' => 'Last 12 months', 'all' => 'All time'];
$r = isset($_GET['d'], $ranges[$_GET['d']]) ? $_GET['d'] : '30';
$to = date('Y-m-d');
$from = $r === 'all' ? '0000-00-00' : date('Y-m-d', strtotime('-' . ((int)$r - 1) . ' days'));

try { $db = io_db(); } catch (Throwable $e) { http_response_code(500); exit('Could not open the database: ' . htmlspecialchars($e->getMessage())); }
$q = function (string $sql, array $a = []) use ($db) { $s = $db->prepare($sql); $s->execute($a); return $s->fetchAll(PDO::FETCH_ASSOC); };
$span = [$from, $to];

// CSV-eksport av en tabell for perioden.
if (isset($_GET['csv'])) {
    $t = $_GET['csv'];
    $sql = [
        'daily'  => 'SELECT day, visitors, views FROM daily WHERE day BETWEEN ? AND ? ORDER BY day',
        'views'  => 'SELECT day, path, n AS views, visits FROM views WHERE day BETWEEN ? AND ? ORDER BY day, path',
        'events' => 'SELECT day, name, page, n FROM events WHERE day BETWEEN ? AND ? ORDER BY day, name, page',
        'dims'   => 'SELECT day, dim, val, n FROM dims WHERE day BETWEEN ? AND ? ORDER BY day, dim, val',
    ][$t] ?? null;
    if (!$sql) { http_response_code(400); exit; }
    $rows = $q($sql, $span);
    header('Content-Type: text/csv; charset=utf-8');
    header('Content-Disposition: attachment; filename="ncs-io-' . $t . '-' . $from . '-' . $to . '.csv"');
    $out = fopen('php://output', 'w');
    if ($rows) fputcsv($out, array_keys($rows[0]));
    foreach ($rows as $row) fputcsv($out, $row);
    exit;
}

$days = $q('SELECT day, visitors, views FROM daily WHERE day BETWEEN ? AND ? ORDER BY day', $span);
$tot = ['visitors' => 0, 'views' => 0];
foreach ($days as $d) { $tot['visitors'] += $d['visitors']; $tot['views'] += $d['views']; }
$today = $q('SELECT visitors, views FROM daily WHERE day = ?', [$to])[0] ?? ['visitors' => 0, 'views' => 0];
$pages = $q('SELECT path, SUM(n) AS views, SUM(visits) AS visits FROM views WHERE day BETWEEN ? AND ? GROUP BY path ORDER BY views DESC', $span);
$sections = [];
foreach ($pages as $p) {
    $sec = explode('/', trim($p['path'], '/'))[0] ?: 'home';
    $sections[$sec] = ($sections[$sec] ?? 0) + $p['views'];
}
arsort($sections);
$events = $q('SELECT name, SUM(n) AS n FROM events WHERE day BETWEEN ? AND ? GROUP BY name ORDER BY n DESC', $span);
$eventPages = $q('SELECT name, page, SUM(n) AS n FROM events WHERE day BETWEEN ? AND ? GROUP BY name, page ORDER BY n DESC LIMIT 30', $span);
$dim = function (string $d) use ($q, $span) {
    return $q('SELECT val, SUM(n) AS n FROM dims WHERE dim = ? AND day BETWEEN ? AND ? GROUP BY val ORDER BY n DESC LIMIT 25', [$d, $span[0], $span[1]]);
};
$hours = array_fill(0, 24, 0);
foreach ($q('SELECT hour, SUM(n) AS n FROM hours WHERE day BETWEEN ? AND ? GROUP BY hour', $span) as $h) $hours[(int)$h['hour']] = (int)$h['n'];

// Alle dager i perioden, også de uten treff (null i grafen blir 0).
$series = [];
if ($days) {
    $start = $r === 'all' ? $days[0]['day'] : $from;
    $byDay = array_column($days, null, 'day');
    // Kalenderdager (ikke 86 400 sekunder), så overgangen til og fra sommertid
    // ikke hopper over en dag.
    $end = new DateTime($to);
    for ($d = new DateTime($start); $d <= $end; $d->modify('+1 day')) {
        $k = $d->format('Y-m-d');
        $series[] = [$k, (int)($byDay[$k]['visitors'] ?? 0), (int)($byDay[$k]['views'] ?? 0)];
    }
}

$e = fn($s) => htmlspecialchars((string)$s, ENT_QUOTES);
$n = fn($v) => number_format((float)$v, 0, '.', ' ');
function io_table(array $rows, array $cols, int $total = 0, string $e = ''): string {
    if (!$rows) return '<p class="muted">No data in this period.</p>';
    $h = '<table><thead><tr>';
    foreach ($cols as [$k, $l, $num]) $h .= '<th' . ($num ? ' class="num"' : '') . '>' . htmlspecialchars($l) . '</th>';
    if ($total) $h .= '<th class="num">Share</th>';
    $h .= '</tr></thead><tbody>';
    foreach ($rows as $row) {
        $h .= '<tr>';
        foreach ($cols as [$k, , $num]) $h .= '<td' . ($num ? ' class="num"' : '') . '>' . ($num ? number_format((float)$row[$k], 0, '.', ' ') : htmlspecialchars((string)$row[$k])) . '</td>';
        if ($total) $h .= '<td class="num">' . number_format(100 * $row[$cols[count($cols) - 1][0]] / $total, 1) . ' %</td>';
        $h .= '</tr>';
    }
    return $h . '</tbody></table>';
}
$dimTable = function (string $d, string $label) use ($dim) {
    $rows = $dim($d);
    $sum = array_sum(array_column($rows, 'n'));
    return io_table($rows, [['val', $label, false], ['n', $d === 'ref' ? 'Page loads' : 'Visitors', true]], (int)$sum);
};
?><!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>NCS Insight · Visitors</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
  :root{--navy:#7b1e45;--muted:#6b7280;--border:#e5e7eb;--bg:#f2f2f2;--card:#fff;--pink:#ce0f69;--light:#f7d0e2;}
  *{box-sizing:border-box} body{margin:0;background:var(--bg);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Inter,sans-serif;color:#1a2332;font-size:14px}
  header{background:#7b1e45;color:#fff;padding:14px 16px} header h1{margin:0;font-size:1.05rem;max-width:1150px;margin:0 auto}
  main{max-width:1150px;margin:0 auto;padding:14px 16px 40px}
  .card{background:var(--card);border-radius:12px;box-shadow:0 1px 8px rgba(0,0,0,.06);padding:14px 16px;margin-bottom:12px}
  h2{font-size:.9rem;color:var(--navy);margin:0 0 10px}
  .pills{display:flex;flex-wrap:wrap;border:1px solid var(--border);border-radius:8px;overflow:hidden;width:max-content;max-width:100%;margin-bottom:12px}
  .pills a{padding:6px 12px;color:var(--muted);text-decoration:none;border-right:1px solid var(--border);font-size:.82rem} .pills a:last-child{border-right:0}
  .pills a.on{background:var(--pink);color:#fff}
  .tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}
  .tile span{display:block;font-size:.68rem;letter-spacing:.04em;text-transform:uppercase;color:var(--muted)} .tile strong{font-size:1.25rem}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:12px}
  .grid .card{margin:0}
  table{width:100%;border-collapse:collapse;font-size:.82rem} th{text-align:left;color:var(--navy);background:#e8edf3;padding:6px 8px}
  td{padding:5px 8px;border-top:1px solid var(--border)} .num{text-align:right;white-space:nowrap}
  .muted{color:var(--muted);font-size:.8rem} .chart{position:relative;height:260px}
  .csv a{color:var(--pink);margin-right:12px;font-size:.8rem}
</style></head>
<body><header><h1>NCS Insight · Visitors</h1></header><main>
  <div class="pills"><?php foreach ($ranges as $k => $l): ?><a class="<?= $k === $r ? 'on' : '' ?>" href="?d=<?= $k ?>"><?= $e($l) ?></a><?php endforeach ?></div>
  <div class="card"><div class="tiles">
    <div class="tile"><span>Visitors</span><strong><?= $n($tot['visitors']) ?></strong><div class="muted">unique per day, summed</div></div>
    <div class="tile"><span>Page views</span><strong><?= $n($tot['views']) ?></strong><div class="muted">section or tab opened</div></div>
    <div class="tile"><span>Views per visitor</span><strong><?= $tot['visitors'] ? number_format($tot['views'] / $tot['visitors'], 1) : '—' ?></strong></div>
    <div class="tile"><span>Today</span><strong><?= $n($today['visitors']) ?> visitors</strong><div class="muted"><?= $n($today['views']) ?> page views</div></div>
  </div></div>
  <div class="card"><h2>Visitors and page views per day</h2><div class="chart"><canvas id="cDays"></canvas></div></div>
  <div class="grid">
    <div class="card"><h2>Sections</h2><?= io_table(array_map(fn($k, $v) => ['s' => $k, 'n' => $v], array_keys($sections), $sections), [['s', 'Section', false], ['n', 'Page views', true]], (int)$tot['views']) ?></div>
    <div class="card"><h2>Time of day (<?= $e(io_config()['timezone']) ?>)</h2><div class="chart" style="height:220px"><canvas id="cHours"></canvas></div></div>
  </div>
  <div class="card" style="margin-top:12px"><h2>Pages</h2><p class="muted">Page views count every time the section or tab is opened; visits count each visitor once per page and day.</p>
    <?= io_table($pages, [['path', 'Page', false], ['visits', 'Visits', true], ['views', 'Page views', true]], (int)$tot['views']) ?></div>
  <div class="grid">
    <div class="card"><h2>Events</h2><?= io_table($events, [['name', 'Event', false], ['n', 'Count', true]]) ?>
      <?php if ($eventPages): ?><h2 style="margin-top:14px">Events by page</h2><?= io_table($eventPages, [['name', 'Event', false], ['page', 'Page', false], ['n', 'Count', true]]) ?><?php endif ?></div>
    <div class="card"><h2>Referrers</h2><?= $dimTable('ref', 'Source') ?></div>
    <div class="card"><h2>Devices</h2><?= $dimTable('device', 'Device') ?></div>
    <div class="card"><h2>Browsers</h2><?= $dimTable('browser', 'Browser') ?></div>
    <div class="card"><h2>Operating systems</h2><?= $dimTable('os', 'System') ?></div>
    <div class="card"><h2>Languages</h2><?= $dimTable('lang', 'Language') ?></div>
  </div>
  <div class="card" style="margin-top:12px"><h2>Export (CSV, this period)</h2><div class="csv">
    <?php foreach (['daily' => 'Per day', 'views' => 'Pages', 'events' => 'Events', 'dims' => 'Devices, browsers, systems, languages, referrers'] as $k => $l): ?>
      <a href="?d=<?= $r ?>&amp;csv=<?= $k ?>"><?= $e($l) ?></a><?php endforeach ?></div>
    <p class="muted">No IP addresses, cookies or identifiers are stored. Unique visitors are counted with a hash that changes every day and is deleted when the day ends. Visitors with Do Not Track or Global Privacy Control are not counted. Ad blockers may block some page views.</p></div>
</main>
<script>
const S = <?= json_encode($series) ?>, H = <?= json_encode($hours) ?>;
const ax = t => ({beginAtZero: true, title: {display: true, text: t, color: '#4a5568', font: {size: 10}}, ticks: {precision: 0, font: {size: 9}}, grid: {color: 'rgba(0,0,0,0.05)'}});
if (S.length) new Chart(document.getElementById('cDays'), {data: {labels: S.map(x => x[0]), datasets: [
    {type: 'bar', label: 'Page views', data: S.map(x => x[2]), backgroundColor: 'rgba(206,15,105,0.25)', borderWidth: 0, yAxisID: 'y'},
    {type: 'line', label: 'Visitors', data: S.map(x => x[1]), borderColor: '#7b1e45', backgroundColor: '#7b1e45', borderWidth: 2, pointRadius: S.length > 60 ? 0 : 2, tension: 0.25, yAxisID: 'y'}]},
  options: {responsive: true, maintainAspectRatio: false, animation: false, interaction: {mode: 'index', intersect: false},
    plugins: {legend: {position: 'bottom', labels: {boxWidth: 12, font: {size: 11}}}},
    scales: {x: {ticks: {maxRotation: 0, autoSkipPadding: 16, font: {size: 9}}, grid: {display: false}}, y: ax('count')}}});
new Chart(document.getElementById('cHours'), {type: 'bar', data: {labels: H.map((_, i) => String(i).padStart(2, '0')), datasets: [
    {label: 'Page views', data: H, backgroundColor: 'rgba(206,15,105,0.6)', borderWidth: 0}]},
  options: {responsive: true, maintainAspectRatio: false, animation: false, plugins: {legend: {display: false}},
    scales: {x: {grid: {display: false}, ticks: {font: {size: 9}}}, y: ax('page views')}}});
</script>
</body></html>
