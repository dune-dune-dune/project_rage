-- Default drone-detection WebSocket feed settings. Previously proposed as
-- DRONE_WS_ENABLED / DRONE_WS_URL in .env; moved to SQLite so the operator can
-- enable it and point it at the detection server from the cockpit ⚙ panel
-- («Налаштування дрон-детекції») without a redeploy — same rationale as the
-- video/network profiles (0002_seed_network.sql).
--
-- Seeded DISABLED: the detection server lives on the far end of the WireGuard
-- tunnel (reachable only from the Jetson), so it stays off until the operator
-- turns it on. The URL default points at the VPN address; adjust in the panel.
INSERT OR IGNORE INTO settings (key, value) VALUES (
    'drone',
    '{"enabled": false, "url": "ws://10.20.100.1:8766"}'
);
