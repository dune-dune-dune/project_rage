"use strict";

// ---------------------------------------------------------------- control input
// Held-key intent. W/A/S/D are momentary (held = moving); F toggles the safety
// (which only gates firing); Space is hold-to-fire; M cycles the fire mode.
const FIRE_MODES = ["short", "medium", "manual"];
// Rotation-speed levels (percent) selectable with keys 1..N; server-provided.
const SPEED =
  window.__SPEED__ && Array.isArray(window.__SPEED__.levels) && window.__SPEED__.levels.length
    ? window.__SPEED__
    : { levels: [50, 100], current: 2 };
const intent = {
  up: false, down: false, left: false, right: false,
  safety: false, fire: false,
  fire_mode: FIRE_MODES.includes(window.__FIRE_MODE__) ? window.__FIRE_MODE__ : "short",
  speed_level: SPEED.current,
};

const KEY_TO_AXIS = {
  KeyW: "up",
  KeyS: "down",
  KeyA: "left",
  KeyD: "right",
  Space: "fire",
};

let dirty = false;

function cycleFireMode() {
  const i = FIRE_MODES.indexOf(intent.fire_mode);
  intent.fire_mode = FIRE_MODES[(i + 1) % FIRE_MODES.length];
  dirty = true;
}

// Control input transport. POST /api/input is the reliable default. The
// WebSocket path (/api/ws) is kept but OFF by default: a half-open socket can
// report readyState===OPEN while silently dropping frames, which black-holes the
// heartbeat, trips the 400 ms deadman and makes the turret clunk (ENABLE drops on
// every neutral packet). Re-enable only after validating it on real hardware.
const USE_WS = false;
let ws = null;
let wsReady = false;
let wsRetryMs = 500; // reconnect backoff, grows to a cap

function sendInputPost() {
  fetch("/api/input", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(intent),
    keepalive: true,
  }).catch(() => {});
}

function sendInput() {
  // Prefer the open WebSocket only when explicitly enabled; else always POST.
  if (USE_WS && wsReady && ws && ws.readyState === WebSocket.OPEN) {
    try {
      ws.send(JSON.stringify(intent));
      return;
    } catch (_) {
      /* fall through to POST on a transient send error */
    }
  }
  sendInputPost();
}

function connectWs() {
  const url = window.location.origin.replace(/^http/, "ws") + "/api/ws";
  try {
    ws = new WebSocket(url);
  } catch (_) {
    scheduleWsReconnect();
    return;
  }
  ws.addEventListener("open", () => {
    wsReady = true;
    wsRetryMs = 500; // reset backoff on a good connection
    sendInput(); // push current intent immediately on (re)connect
  });
  ws.addEventListener("close", () => {
    wsReady = false;
    scheduleWsReconnect();
  });
  ws.addEventListener("error", () => {
    // 'close' fires after 'error' and drives the reconnect; nothing to do here.
    wsReady = false;
  });
}

function scheduleWsReconnect() {
  setTimeout(connectWs, wsRetryMs);
  wsRetryMs = Math.min(wsRetryMs * 2, 5000); // exponential backoff, capped at 5 s
}

if (USE_WS) connectWs();

document.addEventListener("keydown", (e) => {
  if (e.target instanceof HTMLInputElement) return; // don't hijack the sliders
  if (e.code === "Tab") {
    // Switch camera; block the default focus-cycling behaviour.
    e.preventDefault();
    if (!e.repeat) nextCamera();
    return;
  }
  if (e.repeat) return;
  if (e.code === "KeyF") {
    intent.safety = !intent.safety; // toggle (fire arm)
    dirty = true;
    e.preventDefault();
    return;
  }
  if (e.code === "KeyM") {
    cycleFireMode();
    e.preventDefault();
    return;
  }
  if (e.code === "KeyQ") { zoomBy(+ZOOM_STEP); e.preventDefault(); return; }
  if (e.code === "KeyE") { zoomBy(-ZOOM_STEP); e.preventDefault(); return; }
  // Number keys 1..N pick the rotation-speed level (top row and numpad).
  const digit = /^(?:Digit|Numpad)([1-9])$/.exec(e.code);
  if (digit) {
    const n = parseInt(digit[1], 10);
    if (n >= 1 && n <= SPEED.levels.length) {
      intent.speed_level = n;
      dirty = true;
      e.preventDefault();
    }
    return;
  }
  // I toggles AI (YOLO) mode; T toggles auto-track (only meaningful in AI mode).
  if (e.code === "KeyI") { if (window.AI) window.AI.toggle(); e.preventDefault(); return; }
  if (e.code === "KeyT") { if (window.AI) window.AI.toggleTrack(); e.preventDefault(); return; }
  const axis = KEY_TO_AXIS[e.code];
  if (axis) {
    intent[axis] = true;
    dirty = true;
    e.preventDefault();
  }
});

document.addEventListener("keyup", (e) => {
  const axis = KEY_TO_AXIS[e.code];
  if (axis) {
    intent[axis] = false;
    dirty = true;
    e.preventDefault();
  }
});

// Fail-safe: losing focus or hiding the tab releases motion/fire (safety stays
// as last set). The heartbeat below keeps flowing while the tab is merely
// backgrounded, so the turret HOLDS its current position (ENABLE stays on)
// instead of dropping — it only neutralises if the browser is really gone.
function releaseControls() {
  intent.up = intent.down = intent.left = intent.right = intent.fire = false;
  dirty = true;
}
window.addEventListener("blur", releaseControls);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) releaseControls();
});

// Push on change so control feels responsive when the tab is visible.
setInterval(() => {
  if (dirty) {
    dirty = false;
    sendInput();
  }
}, 50);

// Heartbeat so the backend deadman knows we are alive. Driven by a dedicated Web
// Worker whose timers are NOT throttled in a background tab — a plain
// setInterval here would be clamped to >=1 s when the tab is hidden, starving
// the 400 ms deadman and dropping the turret's position hold. Falls back to a
// main-thread interval (throttled in the background) if the worker can't load.
const HEARTBEAT_MS = 150;
// Version-stamp the worker URL so a code change busts its (aggressive) cache.
const HEARTBEAT_WORKER_URL =
  "/static/heartbeat-worker.js" +
  (window.__ASSET_VERSION__ ? "?v=" + window.__ASSET_VERSION__ : "");
let heartbeatWorker = null;
try {
  heartbeatWorker = new Worker(HEARTBEAT_WORKER_URL);
  heartbeatWorker.onmessage = (e) => {
    if (e.data && e.data.type === "tick") sendInput();
  };
  heartbeatWorker.postMessage({ type: "start", intervalMs: HEARTBEAT_MS });
} catch (_) {
  heartbeatWorker = null;
}
if (!heartbeatWorker) setInterval(sendInput, HEARTBEAT_MS); // fallback (bg-throttled)

// ---------------------------------------------------------------------- HUD
// The former bottom-left HUD (#safety / #firemode / #speed badges) was removed;
// its state is shown beside the crosshair (#cp-*) and in the bottom bar. A no-op
// stub keeps the write-only badge assignments below harmless if the element is
// absent — assigning .textContent/.className to a plain object never throws.
const noopBadge = () => ({ textContent: "", className: "" });
const safetyEl = document.getElementById("safety") || noopBadge();
const videoDot = document.getElementById("dot-video"); // video status dot → bottom bar
const turretDot = document.getElementById("dot-turret"); // turret status dot → bottom bar
const batteryEl = document.getElementById("battery");
const moTempEl = document.getElementById("motemp");
const moCurEl = document.getElementById("mocur");
const distEl = document.getElementById("cp-dist"); // rangefinder → crosshair panel
const fireModeEl = document.getElementById("firemode") || noopBadge();
const speedEl = document.getElementById("speed") || noopBadge();
const speedBarEl = document.getElementById("speed-bar"); // speed level → bottom telemetry bar
const zoomEl = document.getElementById("cp-zoom"); // digital zoom → crosshair panel
const safetyIconEl = document.getElementById("cp-safety"); // safety padlock → crosshair panel
const fireModeIconEl = document.getElementById("cp-firemode"); // fire-mode marks → crosshair panel
const keyEls = {};
document.querySelectorAll(".key").forEach((el) => (keyEls[el.dataset.k] = el));

// Paint a connection status dot (green .ok / red .bad / neutral grey) and update
// its hover tooltip, e.g. "Статус турелі: онлайн". Used by the bottom-bar
// turret & video dots.
function setDot(el, label, cls, word) {
  el.classList.toggle("ok", cls === "ok");
  el.classList.toggle("bad", cls === "bad");
  el.dataset.tip = label + ": " + word; // shown by .status-dot:hover::after
}

// Map a video camera name to its lens type shown in the crosshair panel.
function cameraKind(label) {
  if (!label) return "CAM —";
  if (label.includes("95")) return "Ширококутна";
  if (label.includes("96")) return "Вузькокутна";
  return label;
}

// Map an RTCPeerConnection state to the video status word + colour.
function paintVideo(state) {
  if (state === "connected") return setDot(videoDot, "Статус відео", "ok", "онлайн");
  if (state === "connecting" || state === "new" || state === "checking")
    return setDot(videoDot, "Статус відео", "", "підключення");
  return setDot(videoDot, "Статус відео", "bad", "офлайн");
}

function paintKeys() {
  for (const [k, el] of Object.entries(keyEls)) {
    el.classList.toggle("active", !!intent[k]);
  }
  fireModeEl.textContent = "FIRE " + intent.fire_mode.toUpperCase();
  if (fireModeIconEl) fireModeIconEl.dataset.mode = intent.fire_mode; // crosshair-side marks (•/•••/▬)
  speedEl.textContent =
    "SPD " + intent.speed_level + "/" + SPEED.levels.length +
    " · " + SPEED.levels[intent.speed_level - 1] + "%";
  if (speedBarEl)
    speedBarEl.textContent =
      intent.speed_level + " · " + SPEED.levels[intent.speed_level - 1] + "%";
  zoomEl.textContent = zoom.toFixed(1) + "×";
}

// Latest turret angles (degrees, or null before a valid reply), cached for the
// map widgets which read them via window.cockpit.
let lastAzDeg = null;
let lastElDeg = null;

async function pollStatus() {
  try {
    const r = await fetch("/api/status");
    const s = await r.json();
    const armed = !!s.safety_off;
    if (armed) {
      safetyEl.textContent = "ARMED";
      safetyEl.className = "badge armed";
    } else {
      safetyEl.textContent = "SAFE";
      safetyEl.className = "badge safe";
    }
    // Mirror the authoritative safety state on the crosshair: padlock icon
    // (open+red / closed+green) and the reticle colour.
    // #cp-safety is an <svg>: its .className is a read-only SVGAnimatedString,
    // so assigning a string throws. Use setAttribute to set the class.
    if (safetyIconEl) safetyIconEl.setAttribute("class", armed ? "armed" : "safe");
    if (crosshairEl) crosshairEl.style.color = armed ? "#ff4d4d" : "#38ff9e";
    // Turret link / transmit state → bottom-bar status dot.
    if (s.bind_error) {
      setDot(turretDot, "Статус турелі", "bad", "помилка");
    } else if (s.dry_run) {
      setDot(turretDot, "Статус турелі", "", "тест");
    } else {
      const online = (s.link || "offline") === "online";
      setDot(turretDot, "Статус турелі", online ? "ok" : "bad", online ? "онлайн" : "офлайн");
    }
    // Turret-reported angles (azimuth / elevation). null until a valid reply.
    // No longer shown in the bar, but still cached for the map widgets.
    const az = s.angle_rot_deg;
    const el = s.angle_ele_deg;
    lastAzDeg = typeof az === "number" ? az : null;
    lastElDeg = typeof el === "number" ? el : null;
    // Push live angles to the map widgets (no-op until map.js registers).
    if (window.mapWidgets) window.mapWidgets.update();

    // Turret health telemetry (battery / motor temps & currents / rangefinder).
    const num = (v, suffix, digits) =>
      typeof v === "number" ? v.toFixed(digits) + suffix : "—";
    const pair = (o, suffix, digits) => {
      const x = o && o.x, y = o && o.y;
      if (typeof x !== "number" && typeof y !== "number") return "—";
      return num(x, "", digits) + "/" + num(y, suffix, digits);
    };
    const bat = s.battery_percent, batV = s.battery_voltage;
    batteryEl.textContent =
      num(bat, "%", 0) + (typeof batV === "number" ? " " + batV.toFixed(1) + "V" : "");
    batteryEl.classList.toggle("stale", bat === null && batV === null);
    batteryEl.classList.toggle("armed", typeof bat === "number" && bat <= 15); // low-battery warning

    moTempEl.textContent = pair(s.motor_temp, "°", 0);
    moTempEl.classList.toggle("stale", !s.motor_temp || (s.motor_temp.x === null && s.motor_temp.y === null));

    moCurEl.textContent = pair(s.motor_current, "A", 2);
    moCurEl.classList.toggle("stale", !s.motor_current || (s.motor_current.x === null && s.motor_current.y === null));

    // Rangefinder distance → crosshair panel.
    distEl.textContent = num(s.distance_m, " м", 1);
    distEl.classList.toggle("stale", s.distance_m === null || s.distance_m === undefined);
  } catch (_) {}
}
setInterval(() => {
  paintKeys();
  pollStatus();
}, 200);

// ------------------------------------------------------------- digital zoom
// Q/E scale the video element (client-side crop). Persisted in localStorage.
const ZOOM_STEP = 0.2, ZOOM_MIN = 1.0, ZOOM_MAX = 4.0;
let zoom = clampZoom(parseFloat(localStorage.getItem("cockpit.zoom")));
// One <video> per camera (populated by the WHEP block below). Declared here so
// applyZoom can scale them; empty on the first call, filled once cameras load.
let videoEls = [];

function clampZoom(z) {
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Number.isFinite(z) ? z : 1.0));
}
function applyZoom() {
  const t = "scale(" + zoom + ")";
  videoEls.forEach((v) => (v.style.transform = t));
}
function zoomBy(delta) {
  zoom = clampZoom(zoom + delta);
  localStorage.setItem("cockpit.zoom", String(zoom));
  applyZoom();
}
applyZoom();

// ---------------------------------------------------------- crosshair + settings
// Crosshair offset (percent of viewport from centre) is persisted server-side
// via /api/crosshair so it survives restarts and can be reused by other tooling.
const crosshairEl = document.getElementById("crosshair");
const xhEl = document.getElementById("xh");
const xvEl = document.getElementById("xv");
const xhVal = document.getElementById("xh-val");
const xvVal = document.getElementById("xv-val");
const initCross = window.__CROSSHAIR__;
let cross = initCross && typeof initCross === "object" ? { x: +initCross.x || 0, y: +initCross.y || 0 } : { x: 0, y: 0 };

function applyCrosshair() {
  crosshairEl.style.left = 50 + cross.x + "%";
  crosshairEl.style.top = 50 + cross.y + "%";
  xhEl.value = cross.x;
  xvEl.value = cross.y;
  xhVal.textContent = Math.round(cross.x);
  xvVal.textContent = Math.round(cross.y);
}
let crossSaveTimer = null;
function saveCrosshair() {
  clearTimeout(crossSaveTimer);
  crossSaveTimer = setTimeout(() => {
    fetch("/api/crosshair", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cross),
    }).catch(() => {});
  }, 250);
}
xhEl.addEventListener("input", () => { cross.x = parseFloat(xhEl.value) || 0; applyCrosshair(); saveCrosshair(); });
xvEl.addEventListener("input", () => { cross.y = parseFloat(xvEl.value) || 0; applyCrosshair(); saveCrosshair(); });
document.getElementById("xh-reset").addEventListener("click", () => { cross = { x: 0, y: 0 }; applyCrosshair(); saveCrosshair(); });
applyCrosshair();

// ------------------------------------------------------ settings menu (top-left)
// One button toggles a dropdown; each dropdown item opens exactly one settings
// panel (map / crosshair / AI / network / alerts) with mutual exclusion. Panel
// contents and their own handlers live in this file (crosshair, network), ai.js
// (AI) and map.js (map origin) — the controller only shows/hides.
(function initMenu() {
  const menuBtn = document.getElementById("menu-btn");
  const dropdown = document.getElementById("menu-dropdown");
  if (!menuBtn || !dropdown) return;
  const panels = {
    map: document.getElementById("map-settings-form"),
    crosshair: document.getElementById("crosshair-panel"),
    ai: document.getElementById("ai-panel"),
    network: document.getElementById("network-panel"),
    alerts: document.getElementById("alerts-panel"),
  };
  const panelEls = Object.values(panels).filter(Boolean);

  function closePanels() {
    for (const p of panelEls) p.hidden = true;
  }
  function closeAll() {
    dropdown.hidden = true;
    closePanels();
  }
  const anyOpen = () => !dropdown.hidden || panelEls.some((p) => !p.hidden);

  function openPanel(key) {
    const panel = panels[key];
    if (!panel) return;
    const wasOpen = !panel.hidden;
    closeAll();
    if (wasOpen) return; // clicking the active item toggles it closed
    // The map form mirrors server-side cfg; refresh its inputs before showing.
    if (key === "map" && window.mapWidgets && window.mapWidgets.fillForm) {
      window.mapWidgets.fillForm();
    }
    panel.hidden = false;
  }

  // The ⚙ button toggles the whole menu: if anything is open (dropdown OR a
  // panel), it closes it; otherwise it opens the dropdown.
  menuBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (anyOpen()) closeAll();
    else dropdown.hidden = false;
  });
  dropdown.querySelectorAll(".menu-item").forEach((item) => {
    item.addEventListener("click", (e) => {
      e.stopPropagation();
      openPanel(item.dataset.panel);
    });
  });
  // Click anywhere outside the button/dropdown/panels closes everything.
  document.addEventListener("click", (e) => {
    if (!anyOpen()) return;
    if (menuBtn.contains(e.target) || dropdown.contains(e.target)) return;
    if (panelEls.some((p) => p.contains(e.target))) return;
    closeAll();
  });
})();

// ---------------------------------------------------------- network settings
// Which video gateway the browser pulls WHEP from: the turret LAN ("local") or
// the VPN ("remote"). Server-side (SQLite) rather than per-browser, so the
// cockpit comes up in the right mode after a reload/redeploy.
//
// Saving reloads the page on purpose: connectCamera() builds one <video> + one
// RTCPeerConnection per camera at load, so new URLs only apply to a fresh
// document. The server rejects a malformed host/path by keeping the previous
// value — we diff the echoed settings against what was typed and refuse to
// reload if anything was dropped, otherwise a typo would silently do nothing.
(function initNetworkPanel() {
  const panel = document.getElementById("network-panel");
  const saveBtn = document.getElementById("net-save");
  if (!panel || !saveBtn) return;

  const fields = {
    local: {
      host: document.getElementById("net-local-host"),
      cams: [document.getElementById("net-local-cam95"), document.getElementById("net-local-cam96")],
    },
    remote: {
      host: document.getElementById("net-remote-host"),
      cams: [document.getElementById("net-remote-cam95"), document.getElementById("net-remote-cam96")],
    },
  };
  const modeInputs = panel.querySelectorAll('input[name="video-mode"]');
  const note = panel.querySelector(".sp-note");
  const noteText = note ? note.innerHTML : "";

  function fill(cfg) {
    if (!cfg || typeof cfg !== "object") return;
    for (const mode of ["local", "remote"]) {
      const profile = cfg[mode];
      if (!profile) continue;
      fields[mode].host.value = profile.host || "";
      (profile.streams || []).forEach((stream, i) => {
        if (fields[mode].cams[i]) fields[mode].cams[i].value = stream.path || "";
      });
    }
    modeInputs.forEach((input) => { input.checked = input.value === cfg.video_mode; });
  }

  function read() {
    const active = panel.querySelector('input[name="video-mode"]:checked');
    const payload = { video_mode: active ? active.value : "local" };
    for (const mode of ["local", "remote"]) {
      payload[mode] = {
        host: fields[mode].host.value.trim(),
        streams: fields[mode].cams.map((el) => ({ path: el.value.trim() })),
      };
    }
    return payload;
  }

  // True when the server stored exactly what was typed (nothing was rejected).
  function accepted(sent, saved) {
    if (sent.video_mode !== saved.video_mode) return false;
    return ["local", "remote"].every((mode) =>
      sent[mode].host === saved[mode].host &&
      sent[mode].streams.every((s, i) => s.path === saved[mode].streams[i].path));
  }

  saveBtn.addEventListener("click", async () => {
    const payload = read();
    saveBtn.disabled = true;
    try {
      const res = await fetch("/api/network-settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error("HTTP " + res.status);
      const saved = await res.json();
      if (!accepted(payload, saved)) {
        fill(saved);
        if (note) note.innerHTML = "Невірна адреса або назва потоку — значення відхилено.";
        saveBtn.disabled = false;
        return;
      }
      location.reload();
    } catch (err) {
      if (note) note.innerHTML = "Не вдалося зберегти: " + err.message;
      saveBtn.disabled = false;
    }
  });

  panel.addEventListener("input", () => { if (note) note.innerHTML = noteText; });
  fill(window.__NETWORK__);
})();

// -------------------------------------------------------------- WHEP video
// Minimal WHEP (WebRTC-HTTP Egress Protocol) client for MediaMTX. To make TAB
// switching INSTANT, every camera gets its own <video> and a persistent
// RTCPeerConnection established up front; TAB only flips which pre-decoded
// stream is visible — no renegotiation, no ffmpeg cold start, no ICE wait.
const cameraEl = document.getElementById("cp-camtype"); // lens type → crosshair panel
const cameras = Array.isArray(window.__CAMERAS__) ? window.__CAMERAS__ : [];
let camIndex = 0;
// One RTCPeerConnection per camera, so a TAB switch can repaint the video
// status from the target camera's current connection state.
const pcs = [];

// Build one <video> per camera, reusing the static #video for the first.
const baseVideo = document.getElementById("video");
for (let i = 0; i < Math.max(cameras.length, 1); i++) {
  let v = baseVideo;
  if (i > 0) {
    v = document.createElement("video");
    v.autoplay = true;
    v.muted = true;
    v.playsInline = true;
    baseVideo.parentNode.insertBefore(v, baseVideo.nextSibling);
  }
  v.classList.add("cam-video");
  videoEls.push(v);
}

function activeVideo() {
  return videoEls[camIndex];
}

function setActiveCamera(index) {
  camIndex = index;
  videoEls.forEach((v, i) => v.classList.toggle("active", i === index));
  const cam = cameras[index];
  cameraEl.textContent = cameraKind(cam && cam.label);
  applyZoom();
  // Repaint the video status from the newly active camera's connection state.
  paintVideo(cam ? (pcs[index] && pcs[index].connectionState) : "none");
}

async function connectCamera(index) {
  const cam = cameras[index];
  const videoEl = videoEls[index];
  if (!cam) {
    if (index === camIndex) paintVideo("none");
    return;
  }
  // No STUN: LAN deployment, host candidates are local so ICE completes at once.
  const pc = new RTCPeerConnection({});
  pcs[index] = pc;
  pc.addTransceiver("video", { direction: "recvonly" });
  pc.ontrack = (ev) => {
    videoEl.srcObject = ev.streams[0];
    // Minimise the WebRTC playout buffer for low-latency teleop. Over the VPN
    // (WAN RTT ~90 ms) the browser otherwise grows a ~0.5-1 s jitter buffer,
    // which is the perceived video lag. Path jitter here is tiny (~4 ms), so a
    // small target is safe. jitterBufferTarget is the modern API (Chrome/Edge);
    // playoutDelayHint is the legacy fallback. Unsupported browsers keep default.
    try {
      const r =
        ev.receiver ||
        pc.getReceivers().find((x) => x.track && x.track.kind === "video");
      if (r) {
        if ("jitterBufferTarget" in r) r.jitterBufferTarget = 80; // ms
        else if ("playoutDelayHint" in r) r.playoutDelayHint = 0.08; // seconds
      }
    } catch (e) {
      /* best-effort: keep default buffering on unsupported browsers */
    }
  };
  pc.onconnectionstatechange = () => {
    if (index === camIndex) paintVideo(pc.connectionState);
  };

  try {
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    await waitIceGathering(pc);
    const res = await fetch(cam.url, {
      method: "POST",
      headers: { "Content-Type": "application/sdp" },
      body: pc.localDescription.sdp,
    });
    if (!res.ok) throw new Error("WHEP " + res.status);
    const answer = await res.text();
    await pc.setRemoteDescription({ type: "answer", sdp: answer });
  } catch (err) {
    if (index === camIndex) paintVideo("failed");
    console.error("WHEP failed", err);
  }
}

function nextCamera() {
  if (cameras.length < 2) return;
  setActiveCamera((camIndex + 1) % cameras.length);
  // The AI overlay/tracker must drop any locked target on a camera change.
  if (window.AI && window.AI.onCameraSwitch) window.AI.onCameraSwitch();
}

function waitIceGathering(pc) {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => {
      if (pc.iceGatheringState === "complete") {
        pc.removeEventListener("icegatheringstatechange", done);
        resolve();
      }
    };
    pc.addEventListener("icegatheringstatechange", done);
    setTimeout(resolve, 5000); // don't block forever
  });
}

// Show the first camera and pre-connect ALL of them so switching is instant.
setActiveCamera(0);
if (cameras.length) {
  cameras.forEach((_, i) => connectCamera(i));
} else {
  paintVideo("none");
}

// ------------------------------------------------------- shared state for ai.js
// Live getters so ai.js always reads the CURRENT crosshair offset (percent of
// viewport from centre), digital zoom and ACTIVE camera <video> when mapping
// detections and computing the aim error. Exposed rather than duplicated to keep
// one source of truth.
window.cockpit = {
  get cross() { return cross; },     // {x, y} percent of viewport from centre
  get zoom() { return zoom; },       // digital zoom scale applied to the videos
  get camIndex() { return camIndex; },
  get videoEl() { return activeVideo(); },
  get azDeg() { return lastAzDeg; },  // turret azimuth (deg) or null
  get elDeg() { return lastElDeg; },  // turret elevation (deg) or null
};
