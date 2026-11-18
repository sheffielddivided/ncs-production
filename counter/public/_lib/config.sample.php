<?php
// Kopier til config.php (samme mappe) bare hvis noe skal avvike fra
// standardverdiene i io.php. config.php ligger ikke i repoet.
return [
    // Nettsteder som får telle.
    'allowed_hosts' => ['energy.kbak.no'],
    // Full sti til databasefila, utenfor den offentlige mappen. Standard er
    // mappen ncs-io-data ved siden av mappen til subdomenet.
    // 'db_path' => '/var/www/vhosts/kbak.no/ncs-io-data/io.sqlite',
    'timezone' => 'Europe/Oslo',
];
