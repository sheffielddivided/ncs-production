<?php
// Mottak for besøkstelleren. Appen sender ett lite JSON-treff med
// navigator.sendBeacon (Content-Type text/plain, så nettleseren gjør ingen
// CORS-forespørsel først). Svarer alltid 204 uten innhold.
define('IO_ENTRY', 1);
require __DIR__ . '/_lib/io.php';

$cfg = io_config();
$host = '';
foreach (['HTTP_ORIGIN', 'HTTP_REFERER'] as $k) {
    if (!empty($_SERVER[$k])) { $host = strtolower((string)parse_url($_SERVER[$k], PHP_URL_HOST)); break; }
}
$allowed = in_array($host, $cfg['allowed_hosts'], true);
if ($allowed) {
    header('Access-Control-Allow-Origin: ' . ($_SERVER['HTTP_ORIGIN'] ?? ('https://' . $host)));
    header('Vary: Origin');
}
header('Cache-Control: no-store');

if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST' || !$allowed) { http_response_code(204); exit; }

$body = file_get_contents('php://input', false, null, 0, 2048);
$hit = json_decode((string)$body, true);
if (is_array($hit)) {
    try {
        io_record($hit, (string)($_SERVER['REMOTE_ADDR'] ?? ''), (string)($_SERVER['HTTP_USER_AGENT'] ?? ''), $host);
    } catch (Throwable $e) {
        error_log('ncs-io: ' . $e->getMessage());
    }
}
http_response_code(204);
