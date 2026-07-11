// Top-centre heading tape (compass). A horizontal degree ribbon pinned to the
// top-centre of the screen that scrolls with the turret's compass bearing
// (azimuth + north correction, normalised to 0..360 — the SAME value shown by
// the AZIMUTH gauge and the map needle). It is driven by map.js, which owns the
// bearing computation and calls update(bearing) at ~5 Hz from pollStatus.
(function () {
  "use strict";

  const svg = document.getElementById("compass-tape");
  const readout = document.getElementById("compass-val");
  if (!svg) return;

  const SVGNS = "http://www.w3.org/2000/svg";
  // Ribbon geometry in SVG user units (matches the viewBox). The tape shows
  // SPAN_DEG of heading across the full width; a fixed centre index marks the
  // current bearing.
  const W = 480;
  const CX = W / 2;
  const SPAN_DEG = 90;                // total degrees visible across the width
  const PX_PER_DEG = W / SPAN_DEG;
  const BASE_Y = 30;                  // horizontal baseline (ticks hang above it)

  const norm360 = (d) => ((d % 360) + 360) % 360;
  // Ukrainian cardinal abbreviations at 0/90/180/270.
  const CARDINALS = { 0: "Пн", 90: "Сх", 180: "Пд", 270: "Зх" };

  function el(name, attrs, text) {
    const n = document.createElementNS(SVGNS, name);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    if (text != null) n.textContent = text;
    return n;
  }

  // Redraw the tape centred on `bearing` (deg). null → dim + dashes.
  function render(bearing) {
    svg.textContent = "";
    if (typeof bearing !== "number") {
      svg.classList.add("stale");
      if (readout) readout.textContent = "—";
      return;
    }
    svg.classList.remove("stale");
    // Readout shows the rounded bearing (no decimals).
    if (readout) readout.textContent = String(Math.round(norm360(bearing))).padStart(3, "0") + "°";

    // The horizontal reference line the degrees sit on.
    svg.appendChild(el("line", { x1: 0, y1: BASE_Y, x2: W, y2: BASE_Y, class: "c-baseline" }));

    // Ticks every 5° across the visible window; major every 30°; cardinals get
    // their letter. Iterate over absolute (unwrapped) degrees so the tape scrolls
    // continuously through the 360/0 seam, then normalise for the label.
    const half = SPAN_DEG / 2;
    const first = Math.ceil(bearing - half);
    const last = Math.floor(bearing + half);
    for (let d = first; d <= last; d++) {
      const deg = norm360(d);
      if (deg % 5 !== 0) continue;
      const x = CX + (d - bearing) * PX_PER_DEG;
      const cardinal = CARDINALS[deg];
      const major = deg % 30 === 0;
      const len = cardinal ? 14 : major ? 11 : 6;
      svg.appendChild(el("line", {
        x1: x.toFixed(1), y1: BASE_Y - len, x2: x.toFixed(1), y2: BASE_Y,
        class: cardinal ? "c-tick c-card" : major ? "c-tick c-major" : "c-tick",
      }));
      if (cardinal) {
        svg.appendChild(el("text", { x: x.toFixed(1), y: BASE_Y - 17, class: "c-label c-card-label" }, cardinal));
      } else if (major) {
        svg.appendChild(el("text", { x: x.toFixed(1), y: BASE_Y - 15, class: "c-label" }, String(deg)));
      }
    }

    // Fixed centre index: a small accent triangle whose tip touches the baseline
    // from below, marking the exact current heading under the readout box.
    svg.appendChild(el("path", {
      d: `M ${CX} ${BASE_Y + 1} L ${CX - 5} ${BASE_Y + 9} L ${CX + 5} ${BASE_Y + 9} Z`,
      class: "c-index",
    }));
  }

  // Exposed for map.js (which computes the bearing from live azimuth telemetry).
  window.compass = { update: render };
  render(null);
})();
