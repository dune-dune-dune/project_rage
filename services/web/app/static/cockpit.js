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
const speedEl = document.getElementById("speed");
const zoomEl = document.getElementById("zoom");
const keyEls = {};
document.querySelectorAll(".key").forEach((el) => (keyEls[el.dataset.k] = el));

function paintKeys() {
  for (const [k, el] of Object.entries(keyEls)) {
    el.classList.toggle("active", !!intent[k]);
  }
  fireModeEl.textContent = "FIRE " + intent.fire_mode.toUpperCase();
  speedEl.textContent =
    "SPD " + intent.speed_level + "/" + SPEED.levels.length +
    " · " + SPEED.levels[intent.speed_level - 1] + "%";
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
// Minimal WHEP (WebRTC-HTTP Egress Protocol) client for MediaMTX. To make TAB
// switching INSTANT, every camera gets its own <video> and a persistent
// RTCPeerConnection established up front; TAB only flips which pre-decoded
// stream is visible — no renegotiation, no ffmpeg cold start, no ICE wait.
const cameraEl = document.getElementById("camera");
const cameras = Array.isArray(window.__CAMERAS__) ? window.__CAMERAS__ : [];
let camIndex = 0;

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
  cameraEl.textContent = (cam && cam.label) || "CAM —";
  applyZoom();
}

async function connectCamera(index) {
  const cam = cameras[index];
  const videoEl = videoEls[index];
  if (!cam) {
    if (index === camIndex) linkEl.textContent = "NO VIDEO URL";
    return;
  }
  // No STUN: LAN deployment, host candidates are local so ICE completes at once.
  const pc = new RTCPeerConnection({});
  pc.addTransceiver("video", { direction: "recvonly" });
  pc.ontrack = (ev) => { videoEl.srcObject = ev.streams[0]; };
  pc.onconnectionstatechange = () => {
    if (index === camIndex) linkEl.textContent = "VIDEO " + pc.connectionState.toUpperCase();
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
    if (index === camIndex) linkEl.textContent = "NO SIGNAL";
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
  linkEl.textContent = "NO VIDEO URL";
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
};
