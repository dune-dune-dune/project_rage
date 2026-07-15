// Renders live targets (FPV drones / "Молнія" missiles) as pulsing markers on
// the existing Leaflet map. The browser has NO route to the targets VM — the
// Jetson holds that WireGuard tunnel and relays the feed, so this polls
// /api/targets on the cockpit's own origin (see app/targets.py). Icon SVGs and
// the target_type_id selection rule are copied verbatim from the reference.
(function () {
    'use strict';

    const POLL_INTERVAL_MS = 1000;  // the upstream feed updates ~once per second

    function getTargetIconSVG(targetTypeId, targetName) {
        // Молнія (ракета з крилами)
        if (targetTypeId === 2 || targetTypeId === 3) {
            return `
                <rect x="14.5" y="6" width="3" height="18" rx="1.5" fill="#ff4444" stroke="#ffffff" stroke-width="1"/>
                <path d="M 4 16 L 15 12 L 17 12 L 28 16 L 28 20 L 17 16 L 15 16 L 4 20 Z" fill="#ffffff" stroke="#ff4444" stroke-width="1"/>
                <path d="M 15 26 L 16 23 L 17 26 Z" fill="#ffffff" stroke="#ff4444" stroke-width="1"/>
                <rect x="10" y="24" width="12" height="2" fill="#ffffff" stroke="#ff4444" stroke-width="1"/>
            `;
        }
        // FPV (квадрокоптер)
        return `
            <circle cx="16" cy="16" r="4" fill="#ff4444" stroke="#ffffff" stroke-width="2"/>
            <line x1="8" y1="8" x2="12" y2="12" stroke="#ffffff" stroke-width="2"/>
            <line x1="24" y1="8" x2="20" y2="12" stroke="#ffffff" stroke-width="2"/>
            <line x1="8" y1="24" x2="12" y2="20" stroke="#ffffff" stroke-width="2"/>
            <line x1="24" y1="24" x2="20" y2="20" stroke="#ffffff" stroke-width="2"/>
            <circle cx="8" cy="8" r="3" fill="#ffffff" stroke="#ff4444" stroke-width="1"/>
            <circle cx="24" cy="8" r="3" fill="#ffffff" stroke="#ff4444" stroke-width="1"/>
            <circle cx="8" cy="24" r="3" fill="#ffffff" stroke="#ff4444" stroke-width="1"/>
            <circle cx="24" cy="24" r="3" fill="#ffffff" stroke="#ff4444" stroke-width="1"/>
        `;
    }

    function getTargetIcon(target) {
        const innerSVG = getTargetIconSVG(target.target_type_id, target.target_name);
        return L.divIcon({
            className: 'target-marker',
            html: `<div class="target-marker-container">
                <div class="target-icon">
                    <svg width="30" height="30" viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg">
                        ${innerSVG}
                    </svg>
                </div>
            </div>`,
            iconSize: [30, 30],
            iconAnchor: [15, 15],
            popupAnchor: [0, -15]
        });
    }

    let targetMarkers = {};
    function updateTargetMarkers(targets) {
        // The Leaflet map lives inside map.js; it exposes the instance via the getter.
        const map = window.mapWidgets && window.mapWidgets.map;
        if (!map) return;
        const data = targets || {};
        const incoming = new Set(Object.keys(data));
        Object.keys(targetMarkers).forEach(id => {
            if (!incoming.has(id)) { map.removeLayer(targetMarkers[id]); delete targetMarkers[id]; }
        });
        Object.values(data).forEach(target => {
            const lat = parseFloat(target.actual_lat), lon = parseFloat(target.actual_lon);
            if (isNaN(lat) || isNaN(lon)) return;
            const existing = targetMarkers[target.id];
            if (existing) { existing.setLatLng([lat, lon]); existing.setIcon(getTargetIcon(target)); }
            else { targetMarkers[target.id] = L.marker([lat, lon], { icon: getTargetIcon(target) }).addTo(map); }
        });
    }

    // Expose for manual/console testing without the relay running.
    window.updateTargetMarkers = updateTargetMarkers;

    // Poll the cockpit's own origin; the Jetson relay does the WG-tunnelled work.
    let inFlight = false;
    async function pollTargets() {
        if (inFlight) return;
        inFlight = true;
        try {
            const res = await fetch('/api/targets', { cache: 'no-store' });
            if (res.ok) {
                const data = await res.json();
                updateTargetMarkers((data && data.targets) || {});
            }
        } catch (e) {
            // Transient network/relay hiccup — keep the last markers, retry next tick.
        } finally {
            inFlight = false;
        }
    }
    setInterval(pollTargets, POLL_INTERVAL_MS);
    pollTargets();
})();
