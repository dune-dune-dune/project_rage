"use strict";

// ---------------------------------------------------------------- control input
// Held-key intent. W/A/S/D are momentary (held = moving); F toggles the safety
// (which only gates firing); Space is hold-to-fire; M cycles the fire mode.
const FIRE_MODES = ["short", "medium", "manual"];
const intent = {
  up: false, down: false, left: false, right: false,
  safety: false, fire: false,
  fire_mode: FIRE_MODES.includes(window.__FIRE_MODE__) ? window.__FIRE_MODE__ : "short",
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

function sendInput() {
  fetch("/api/input", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(intent),
    keepalive: true,
  }).catch(() => {});
}

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

// Fail-safe: losing focus releases everything (safety stays as last set, but
// with no motion/fire the next deadman tick neutralises the turret anyway).
window.addEventListener("blur", () => {
  intent.up = intent.down = intent.left = intent.right = intent.fire = false;
  dirty = true;
});

// Push on change and as a heartbeat so the backend deadman knows we are alive.
setInterval(() => {
  if (dirty) {
    dirty = false;
    sendInput();
  }
}, 50);
setInterval(sendInput, 150); // heartbeat

// ---------------------------------------------------------------------- HUD
const safetyEl = document.getElementById("safety");
const linkEl = document.getElementById("link");
const turretEl = document.getElementById("turret");
const fireModeEl = document.getElementById("firemode");
const zoomEl = document.getElementById("zoom");
const keyEls = {};
document.querySelectorAll(".key").forEach((el) => (keyEls[el.dataset.k] = el));

function paintKeys() {
  for (const [k, el] of Object.entries(keyEls)) {
    el.classList.toggle("active", !!intent[k]);
  }
  fireModeEl.textContent = "FIRE " + intent.fire_mode.toUpperCase();
  zoomEl.textContent = zoom.toFixed(1) + "×";
}

async function pollStatus() {
  try {
    const r = await fetch("/api/status");
    const s = await r.json();
    if (s.safety_off) {
      safetyEl.textContent = "ARMED";
      safetyEl.className = "badge armed";
    } else {
      safetyEl.textContent = "SAFE";
      safetyEl.className = "badge safe";
    }
    // Turret link / transmit state.
    if (s.bind_error) {
      turretEl.textContent = "TURRET BIND ERR";
      turretEl.className = "badge armed";
    } else if (s.dry_run) {
      turretEl.textContent = "TURRET DRY";
      turretEl.className = "badge dry";
    } else {
      const link = (s.link || "offline").toUpperCase();
      turretEl.textContent = "TURRET " + link;
      turretEl.className = "badge " + (link === "ONLINE" ? "safe" : "armed");
    }
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

function clampZoom(z) {
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Number.isFinite(z) ? z : 1.0));
}
function applyZoom() {
  document.getElementById("video").style.transform = "scale(" + zoom + ")";
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
const settingsBtn = document.getElementById("settings-btn");
const settingsPanel = document.getElementById("settings-panel");
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
settingsBtn.addEventListener("click", () => { settingsPanel.hidden = !settingsPanel.hidden; });
applyCrosshair();

// -------------------------------------------------------------- WHEP video
// Minimal WHEP (WebRTC-HTTP Egress Protocol) client for MediaMTX, with a
// TAB camera switcher that tears down and reconnects the peer connection.
const cameraEl = document.getElementById("camera");
const cameras = Array.isArray(window.__CAMERAS__) ? window.__CAMERAS__ : [];
const videoEl = document.getElementById("video");
let camIndex = 0;
let currentPc = null;
let switchToken = 0; // guards against overlapping async switches

async function connectCamera(index) {
  const cam = cameras[index];
  const token = ++switchToken;

  if (currentPc) {
    try { currentPc.close(); } catch (_) {}
    currentPc = null;
  }
  if (!cam) {
    linkEl.textContent = "NO VIDEO URL";
    cameraEl.textContent = "CAM —";
    return;
  }
  cameraEl.textContent = cam.label || "CAM";
  linkEl.textContent = "VIDEO …";

  const pc = new RTCPeerConnection({
    iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
  });
  currentPc = pc;
  pc.addTransceiver("video", { direction: "recvonly" });
  pc.ontrack = (ev) => {
    if (token === switchToken) videoEl.srcObject = ev.streams[0];
  };
  pc.onconnectionstatechange = () => {
    if (pc === currentPc) linkEl.textContent = "VIDEO " + pc.connectionState.toUpperCase();
  };

  try {
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    await waitIceGathering(pc);
    if (token !== switchToken) return; // superseded by a newer switch
    const res = await fetch(cam.url, {
      method: "POST",
      headers: { "Content-Type": "application/sdp" },
      body: pc.localDescription.sdp,
    });
    if (!res.ok) throw new Error("WHEP " + res.status);
    const answer = await res.text();
    if (token !== switchToken) return;
    await pc.setRemoteDescription({ type: "answer", sdp: answer });
  } catch (err) {
    if (token === switchToken) linkEl.textContent = "NO SIGNAL";
    console.error("WHEP failed", err);
  }
}

function nextCamera() {
  if (cameras.length < 2) return;
  camIndex = (camIndex + 1) % cameras.length;
  connectCamera(camIndex);
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

connectCamera(camIndex);

// ------------------------------------------------------- shared state for ai.js
// Live getters so ai.js always reads the CURRENT crosshair offset (percent of
// viewport from centre), digital zoom and active camera when mapping detections
// and computing the aim error. Exposed rather than duplicated to keep one source
// of truth.
window.cockpit = {
  get cross() { return cross; },     // {x, y} percent of viewport from centre
  get zoom() { return zoom; },       // digital zoom scale applied to #video
  get camIndex() { return camIndex; },
  videoEl,
};
