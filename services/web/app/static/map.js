// Top-right map cluster: a Leaflet map centred on a fixed origin drawing the
// turret's azimuth sector, plus two small SVG gauges (azimuth range, elevation
// range). Live turret angles come from window.cockpit (fed by pollStatus in
// cockpit.js); settings persist server-side via /api/map-settings.
//
// Azimuth → map bearing mapping: the turret's telemetry azimuth ``az`` (deg) maps
// to a compass bearing by adding the operator-set north correction:
//   bearing = az + north_correction
// so north_correction is the compass bearing at which the turret reads azimuth 0.
// az_min/az_max/ele_min/ele_max are FIXED constants (not user-editable) used only
// to draw the azimuth sector and the two gauges.
(function () {
  "use strict";

  // Fixed sector radius drawn on the map, in metres.
  const SECTOR_RADIUS_M = 300;
  const EARTH_R = 6371000;

  // --- settings ---------------------------------------------------------------
  const DEFAULTS = {
    lat: 0, lon: 0, north_correction: 0,
    az_min: -72, az_max: 72, ele_min: -8, ele_max: 30,
  };
  let cfg = Object.assign({}, DEFAULTS, window.__MAP__ || {});

  // --- DOM --------------------------------------------------------------------
  const canvas = document.getElementById("map-canvas");
  // Map settings live in the top-left menu now; there is no on-map ⚙ button.
  const form = document.getElementById("map-settings-form");
  const saveBtn = document.getElementById("map-save");
  const inputs = {
    lat: document.getElementById("map-lat"),
    lon: document.getElementById("map-lon"),
    north_correction: document.getElementById("map-north-corr"),
  };

  // --- geo helpers ------------------------------------------------------------
  // Destination point given start lat/lon, compass bearing (deg) and distance
  // (m) — standard great-circle "direct" formula.
  function destPoint(lat, lon, bearingDeg, distM) {
    const d = distM / EARTH_R;
    const th = (bearingDeg * Math.PI) / 180;
    const p1 = (lat * Math.PI) / 180;
    const l1 = (lon * Math.PI) / 180;
    const p2 = Math.asin(
      Math.sin(p1) * Math.cos(d) + Math.cos(p1) * Math.sin(d) * Math.cos(th)
    );
    const l2 =
      l1 +
      Math.atan2(
        Math.sin(th) * Math.sin(d) * Math.cos(p1),
        Math.cos(d) - Math.sin(p1) * Math.sin(p2)
      );
    return [(p2 * 180) / Math.PI, (l2 * 180) / Math.PI];
  }

  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const norm360 = (deg) => ((deg % 360) + 360) % 360; // 0..360
  // Live azimuth telemetry → compass bearing (turret azimuth + north correction).
  function azToBearing(azDeg) {
    return azDeg + cfg.north_correction;
  }

  // --- Leaflet map ------------------------------------------------------------
  let map = null;
  let marker = null;
  let sector = null; // sector polygon (full azimuth range)
  let needle = null; // live azimuth line

  function sectorLatLngs(startBearing, endBearing) {
    const pts = [[cfg.lat, cfg.lon]];
    const steps = 32;
    for (let i = 0; i <= steps; i++) {
      const b = startBearing + ((endBearing - startBearing) * i) / steps;
      pts.push(destPoint(cfg.lat, cfg.lon, b, SECTOR_RADIUS_M));
    }
    return pts;
  }

  // Turret emblem marker (badge style, static — it does NOT rotate with azimuth;
  // the sector polygon + needle show heading). Rendered as a Leaflet divIcon so
  // no image asset is needed (keeps the map offline-friendly like the vendored
  // Leaflet); the ".turret-marker" CSS strips divIcon's default white box.
  const TURRET_EMBLEM = `
<svg viewBox="0 0 40 40" width="40" height="40" xmlns="http://www.w3.org/2000/svg">
  <circle cx="20" cy="20" r="18" fill="#0b1116" stroke="#38ff9e" stroke-width="2"/>
  <!-- top-down turret: ring base, hub, and a barrel pointing forward (up) -->
  <circle cx="20" cy="21" r="8" fill="none" stroke="#38ff9e" stroke-width="2.4"/>
  <rect x="18" y="5" width="4" height="16" rx="1.4" fill="#38ff9e"/>
  <rect x="16.5" y="4.5" width="7" height="3" rx="1.2" fill="#38ff9e"/>
  <circle cx="20" cy="21" r="2.6" fill="#38ff9e"/>
</svg>`;

  // --- drone-detection targets ------------------------------------------------
  // Air targets streamed from the server (/api/status.targets). FPV = drone icon,
  // "Molnia" (types 2/3) = plane icon; the label shows "altitude / video_freq".
  const FPV_ICON = `
<svg width="32" height="32" viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg">
  <circle cx="16" cy="16" r="4" fill="#ff4444" stroke="#ffffff" stroke-width="2"></circle>
  <line x1="8" y1="8" x2="12" y2="12" stroke="#ffffff" stroke-width="2"></line>
  <line x1="24" y1="8" x2="20" y2="12" stroke="#ffffff" stroke-width="2"></line>
  <line x1="8" y1="24" x2="12" y2="20" stroke="#ffffff" stroke-width="2"></line>
  <line x1="24" y1="24" x2="20" y2="20" stroke="#ffffff" stroke-width="2"></line>
  <circle cx="8" cy="8" r="3" fill="#ffffff" stroke="#ff4444" stroke-width="1"></circle>
  <circle cx="24" cy="8" r="3" fill="#ffffff" stroke="#ff4444" stroke-width="1"></circle>
  <circle cx="8" cy="24" r="3" fill="#ffffff" stroke="#ff4444" stroke-width="1"></circle>
  <circle cx="24" cy="24" r="3" fill="#ffffff" stroke="#ff4444" stroke-width="1"></circle>
</svg>`;
  const MOLNIA_ICON = `
<svg width="32" height="32" viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg">
  <rect x="14.5" y="6" width="3" height="18" rx="1.5" fill="#ff4444" stroke="#ffffff" stroke-width="1"></rect>
  <path d="M 4 16 L 15 12 L 17 12 L 28 16 L 28 20 L 17 16 L 15 16 L 4 20 Z" fill="#ffffff" stroke="#ff4444" stroke-width="1"></path>
  <path d="M 15 26 L 16 23 L 17 26 Z" fill="#ffffff" stroke="#ff4444" stroke-width="1"></path>
  <rect x="10" y="24" width="12" height="2" fill="#ffffff" stroke="#ff4444" stroke-width="1"></rect>
</svg>`;

  let targetLayer = null; // L.layerGroup holding all target markers
  const targetMarkers = new Map(); // target id -> { marker, html }

  // Build the divIcon HTML for one target (icon by kind + freq/altitude label).
  function targetHtml(t) {
    const icon = t.kind === "fpv" ? FPV_ICON : MOLNIA_ICON;
    const wrap = t.kind === "fpv" ? "drone-icon" : "plane-icon";
    const alt = t.altitude ? t.altitude : "-";
    const freq = t.video_freq != null ? t.video_freq : "-";
    return (
      `<div class="target-marker-container">` +
      `<div class="${wrap}">${icon}</div>` +
      `<div class="target-frequency-label">${alt} / ${freq}</div>` +
      `</div>`
    );
  }

  function makeTargetIcon(html) {
    return L.divIcon({
      html,
      className: "target-marker",
      iconSize: [32, 48],
      iconAnchor: [16, 16], // anchor on the icon body centre, label hangs below
    });
  }

  // Reconcile the live target list onto the map (keyed by id, no flicker): move
  // existing markers, add new ones, drop the gone. Called at 5 Hz from cockpit.js.
  function setTargets(list) {
    if (!map || !targetLayer) return;
    const seen = new Set();
    for (const t of Array.isArray(list) ? list : []) {
      if (t == null || typeof t.lat !== "number" || typeof t.lon !== "number") continue;
      const id = String(t.id);
      seen.add(id);
      const html = targetHtml(t);
      const existing = targetMarkers.get(id);
      if (existing) {
        existing.marker.setLatLng([t.lat, t.lon]);
        if (existing.html !== html) {
          existing.marker.setIcon(makeTargetIcon(html));
          existing.html = html;
        }
      } else {
        const marker = L.marker([t.lat, t.lon], {
          icon: makeTargetIcon(html),
          interactive: false,
        }).addTo(targetLayer);
        targetMarkers.set(id, { marker, html });
      }
    }
    // Remove markers whose target vanished from the feed.
    for (const [id, entry] of targetMarkers) {
      if (!seen.has(id)) {
        targetLayer.removeLayer(entry.marker);
        targetMarkers.delete(id);
      }
    }
  }

  function initMap() {
    if (typeof L === "undefined" || !canvas) return; // Leaflet not vendored
    map = L.map(canvas, {
      center: [cfg.lat, cfg.lon],
      zoom: 15,
      zoomControl: false,        // no +/- buttons
      attributionControl: false, // no "Leaflet" attribution
      maxZoom: 16,               // Dark Gray Canvas service tops out at z16
    });
    // Dark basemap (Esri World Dark Gray Canvas): base tiles + labels overlay.
    L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}", {
      maxZoom: 16,
    }).addTo(map);
    L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}", {
      maxZoom: 16,
    }).addTo(map);
    marker = L.marker([cfg.lat, cfg.lon], {
      icon: L.divIcon({
        html: TURRET_EMBLEM,
        className: "turret-marker",
        iconSize: [40, 40],
        iconAnchor: [20, 20], // centre the emblem on the origin
      }),
      interactive: false,
    }).addTo(map);
    sector = L.polygon([], {
      color: "#38ff9e", weight: 1, opacity: 0.6,
      fillColor: "#38ff9e", fillOpacity: 0.12,
    }).addTo(map);
    needle = L.polyline([], { color: "#38ff9e", weight: 3, opacity: 0.9 }).addTo(map);
    // Target markers live in their own layer group above the sector/needle.
    targetLayer = L.layerGroup().addTo(map);
    redrawMap();
    // The container may not have its final size yet at construction time.
    setTimeout(() => map && map.invalidateSize(), 200);
  }

  function redrawMap() {
    if (!map) return;
    map.setView([cfg.lat, cfg.lon], map.getZoom());
    marker.setLatLng([cfg.lat, cfg.lon]);
    // Sector spans the fixed azimuth range, shifted by the north correction.
    const start = cfg.az_min + cfg.north_correction;
    const end = cfg.az_max + cfg.north_correction;
    const ring = sectorLatLngs(start, end);
    sector.setLatLngs(ring);
    map.fitBounds(sector.getBounds(), { padding: [10, 10] });
    updateMapNeedle();
  }

  function updateMapNeedle() {
    if (!map) return;
    const az = window.cockpit ? window.cockpit.azDeg : null;
    if (typeof az !== "number") {
      needle.setLatLngs([]);
      return;
    }
    const tip = destPoint(cfg.lat, cfg.lon, azToBearing(az), SECTOR_RADIUS_M);
    needle.setLatLngs([[cfg.lat, cfg.lon], tip]);
  }

  // --- SVG gauges -------------------------------------------------------------
  // Each gauge is drawn in a 0..100 viewBox. A gauge config maps an angle value
  // to an (x, y) point on the fan, so the sector and needle share one projection.
  const SVGNS = "http://www.w3.org/2000/svg";
  function el(name, attrs) {
    const n = document.createElementNS(SVGNS, name);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    return n;
  }

  // Azimuth gauge: top-down fan, 0° forward = straight up, +deg clockwise (right).
  // The needle shows the turret's mechanical position (raw azimuth within its
  // range); the displayed value is the compass bearing (azimuth + north
  // correction, normalised to 0..360).
  const azGauge = {
    svg: document.getElementById("az-gauge"),
    val: document.getElementById("az-gauge-val"),
    cx: 50, cy: 62, r: 40,
    point(v) {
      const a = (v * Math.PI) / 180;
      return [this.cx + this.r * Math.sin(a), this.cy - this.r * Math.cos(a)];
    },
    lo: () => cfg.az_min,
    hi: () => cfg.az_max,
    display: (v) => norm360(azToBearing(v)),
  };
  // Elevation gauge: side fan, 0° = horizontal (right), +deg up.
  const elGauge = {
    svg: document.getElementById("el-gauge"),
    val: document.getElementById("el-gauge-val"),
    cx: 26, cy: 74, r: 52,
    point(v) {
      const a = (v * Math.PI) / 180;
      return [this.cx + this.r * Math.cos(a), this.cy - this.r * Math.sin(a)];
    },
    lo: () => cfg.ele_min,
    hi: () => cfg.ele_max,
  };

  function buildGauge(g) {
    if (!g.svg) return;
    g.svg.textContent = "";
    const lo = g.lo(), hi = g.hi();
    // Sector fill spanning [lo, hi].
    let d = `M ${g.cx} ${g.cy}`;
    const steps = 24;
    for (let i = 0; i <= steps; i++) {
      const v = lo + ((hi - lo) * i) / steps;
      const [x, y] = g.point(v);
      d += ` L ${x.toFixed(2)} ${y.toFixed(2)}`;
    }
    d += " Z";
    g.svg.appendChild(el("path", { d, class: "g-sector" }));
    // Bounding ticks at lo / hi.
    for (const v of [lo, hi]) {
      const [x, y] = g.point(v);
      g.svg.appendChild(el("line", {
        x1: g.cx, y1: g.cy, x2: x.toFixed(2), y2: y.toFixed(2), class: "g-tick",
      }));
    }
    // Live needle (updated in updateGauge).
    g.needle = el("line", { x1: g.cx, y1: g.cy, x2: g.cx, y2: g.cy, class: "g-needle" });
    g.svg.appendChild(g.needle);
    // Centre dot.
    g.svg.appendChild(el("circle", { cx: g.cx, cy: g.cy, r: 1.8, fill: "#38ff9e" }));
  }

  function updateGauge(g, value) {
    if (!g.svg || !g.needle) return;
    const wrap = g.svg.parentElement; // .gauge
    if (typeof value !== "number") {
      wrap.classList.add("stale");
      g.needle.setAttribute("x2", g.cx);
      g.needle.setAttribute("y2", g.cy);
      if (g.val) g.val.textContent = "—";
      return;
    }
    wrap.classList.remove("stale");
    const [x, y] = g.point(clamp(value, g.lo(), g.hi()));
    g.needle.setAttribute("x2", x.toFixed(2));
    g.needle.setAttribute("y2", y.toFixed(2));
    // Needle uses the raw value; the shown number may be transformed (azimuth →
    // compass bearing with the north correction applied).
    const shown = g.display ? g.display(value) : value;
    if (g.val) g.val.textContent = shown.toFixed(1) + "°";
  }

  // --- live update (called from cockpit.js pollStatus) ------------------------
  function update() {
    const az = window.cockpit ? window.cockpit.azDeg : null;
    const ele = window.cockpit ? window.cockpit.elDeg : null;
    updateGauge(azGauge, typeof az === "number" ? az : null);
    updateGauge(elGauge, typeof ele === "number" ? ele : null);
    updateMapNeedle();
    // Drive the top-centre compass tape with the same compass bearing the
    // azimuth gauge / map needle use (azimuth + north correction, 0..360).
    if (window.compass) {
      window.compass.update(typeof az === "number" ? norm360(azToBearing(az)) : null);
    }
  }

  // Re-measure the map after its container changed size (the grid<->control
  // toggle moves #map-widgets between the full top half and the corner). Leaflet
  // caches the container size, so without invalidateSize() the tiles/sector are
  // laid out for the old box and render half-grey until the next interaction.
  function relayout() {
    if (!map) return;
    map.invalidateSize();
    redrawMap();
  }

  // --- settings form ----------------------------------------------------------
  function fillForm() {
    for (const k in inputs) if (inputs[k]) inputs[k].value = cfg[k];
  }

  function readForm() {
    const num = (elm, def) => {
      const v = parseFloat(elm && elm.value);
      return Number.isFinite(v) ? v : def;
    };
    return {
      lat: num(inputs.lat, cfg.lat),
      lon: num(inputs.lon, cfg.lon),
      north_correction: num(inputs.north_correction, cfg.north_correction),
    };
  }

  if (saveBtn) {
    saveBtn.addEventListener("click", async () => {
      const payload = readForm();
      try {
        const r = await fetch("/api/map-settings", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        cfg = Object.assign({}, DEFAULTS, await r.json()); // server-normalised
      } catch (_) {
        cfg = Object.assign({}, cfg, payload); // offline fallback: apply locally
      }
      if (form) form.hidden = true;
      buildGauge(azGauge);
      buildGauge(elGauge);
      redrawMap();
      update();
    });
  }

  // --- init -------------------------------------------------------------------
  fillForm();
  buildGauge(azGauge);
  buildGauge(elGauge);
  initMap();
  update();

  // Exposed for cockpit.js: update() drives live gauges/needle at 5 Hz;
  // fillForm() lets the top-left menu refresh the map inputs before showing;
  // relayout() re-measures the map after the grid<->control view toggle;
  // setTargets() reconciles the drone-detection markers (also at 5 Hz).
  window.mapWidgets = { update, fillForm, relayout, setTargets };
})();
