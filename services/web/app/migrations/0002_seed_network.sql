-- Default video/network profiles, previously hard-coded in .env as WHEP_URL +
-- VIDEO_GATEWAY_HOST_IP. 'local' = turret LAN, 'remote' = WireGuard VPN.
--
-- Only the 'network' section is seeded here. crosshair / ai / map are NOT: their
-- absence is what triggers the one-time import of the legacy JSON files (see
-- db.import_legacy_json). Seeding them would silently discard the operator's
-- saved crosshair offset on the first boot after this migration.
INSERT OR IGNORE INTO settings (key, value) VALUES (
    'network',
    '{"video_mode":"local",
      "local":{"host":"192.168.88.33",
               "streams":[{"label":"CAM 95","path":"cam95_h264"},
                          {"label":"CAM 96","path":"cam96_h264"}]},
      "remote":{"host":"10.20.100.1",
                "streams":[{"label":"CAM 95","path":"cam95_main"},
                           {"label":"CAM 96","path":"cam96_main"}]}}'
);
