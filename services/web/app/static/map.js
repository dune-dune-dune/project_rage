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
  const settingsBtn = document.getElementById("map-settings-btn");
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

  function initMap() {
    if (typeof L === "undefined" || !canvas) return; // Leaflet not vendored
    map = L.map(canvas, {
      center: [cfg.lat, cfg.lon],
      zoom: 15,
      zoomControl: true,
      attributionControl: true,
    });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: "© OpenStreetMap",
    }).addTo(map);
    marker = L.marker([cfg.lat, cfg.lon]).addTo(map);
    sector = L.polygon([], {
      color: "#38ff9e", weight: 1, opacity: 0.6,
      fillColor: "#38ff9e", fillOpacity: 0.12,
    }).addTo(map);
    needle = L.polyline([], { color: "#38ff9e", weight: 3, opacity: 0.9 }).addTo(map);
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

  if (settingsBtn && form) {
    settingsBtn.addEventListener("click", () => {
      if (form.hidden) fillForm();
      form.hidden = !form.hidden;
    });
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

  // Exposed so cockpit.js pollStatus() can drive live updates at its 5 Hz cadence.
  window.mapWidgets = { update };
})();
