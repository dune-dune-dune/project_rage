"use strict";

// =============================================================================
// AI mode: browser-side detection + turret auto-track. The I key cycles three
// modes:  OFF -> AI ON (YOLO) -> AI CUSTOM (pixel motion) -> OFF.
//
//  * AI ON     — YOLO (ONNX Runtime Web) in a Web Worker on the live <video>.
//  * AI CUSTOM — pixel motion detector on the main thread: consecutive frames
//                are diffed; where the colour changes by more than the ⚙ "motion
//                threshold" %, pixels are marked moving, clustered into blobs,
//                and blobs whose size exceeds the ⚙ "min object size" (px) are
//                flagged as a drone. No model needed.
//  * T         — (any AI mode) lock the target nearest the crosshair and drive
//                the turret so it centres on the CROSSHAIR (offset included).
//
// Inference/detection never blocks the input path (YOLO runs off-thread; motion
// is cheap), so manual control stays smooth. Firing is never automated.
// =============================================================================

const AI = (() => {
  const cfg = window.__AI__ || {};
  const IMGSZ = cfg.imgsz || 640;
  const GAIN = typeof cfg.gain === "number" ? cfg.gain : 2.5;
  const DEADZONE = typeof cfg.deadzone === "number" ? cfg.deadzone : 0.02;
  const MAX_VEL = typeof cfg.max_velocity === "number" ? cfg.max_velocity : 0.5;

  // Operator-tunable thresholds (persisted server-side via ⚙ panel).
  let conf = typeof cfg.conf === "number" ? cfg.conf : 0.7;            // YOLO confidence
  let minSize = typeof cfg.min_size === "number" ? cfg.min_size : 24;   // source-frame px (both modes)
  let motionThresh = typeof cfg.motion_thresh === "number" ? cfg.motion_thresh : 15; // Custom colour-diff %

  const canvas = document.getElementById("detections");
  const ctx = canvas.getContext("2d");
  // The former left HUD (#ai / #track badges) was removed; AI/track state is
  // shown beside the crosshair (#cp-ai / #cp-track). A no-op stub keeps the
  // write-only badge assignments below harmless when the element is absent.
  const aiBadge = document.getElementById("ai") || { textContent: "", className: "" };
  const trackBadge = document.getElementById("track") || { textContent: "", className: "" };
  // Crosshair-side squares (mirror the badges next to the reticle).
  const aiBox = document.getElementById("cp-ai");
  const aiBoxLabel = aiBox && aiBox.querySelector(".cp-ai-label");
  const trackBox = document.getElementById("cp-track");

  // YOLO frame-capture buffer — IDENTICAL to the proven pipeline (2D canvas,
  // drawImage(video), black letterbox, getImageData); only inference is offloaded.
  const pre = document.createElement("canvas");
  pre.width = IMGSZ;
  pre.height = IMGSZ;
  const preCtx = pre.getContext("2d", { willReadFrequently: true });

  // Custom (motion) downscaled buffer.
  const MOTION_W = 192;                 // working width; height follows aspect
  const MOTION_MS = 45;                 // ~22 Hz motion sampling
  let maxShift = typeof cfg.max_shift === "number" ? cfg.max_shift : 16; // ego-motion search range (px)
  const GLOBAL_MOTION_FRAC = 0.40;      // if more than this fraction "moves", frame is unreliable
  const motionCanvas = document.createElement("canvas");
  const mctx = motionCanvas.getContext("2d", { willReadFrequently: true });
  let prevPixels = null;                // previous frame RGBA
  let prevCol = null, prevRow = null;   // previous luminance projection profiles
  let lastMotionAt = 0;

  let worker = null;
  let workerReady = false;
  let workerLoading = null;
  let busy = false;        // a YOLO frame is in flight to the worker
  let classNames = {};
  let lastYoloLog = 0;     // throttle for the diagnostic console log

  let mode = "off";        // "off" | "yolo" | "custom"
  let trackOn = false;
  let running = false;     // loop guard
  let locked = null;       // {x, y, det} normalised frame centre of the tracked target
  let lastVW = 0, lastVH = 0;

  // Auto-track command, DECOUPLED from the detection rate: detection only updates
  // this target velocity, while a fast fixed-rate timer keeps POSTing it, so the
  // turret tracks smoothly and the server aim never times out between frames.
  let currentAim = { active: false, rot: 0, ele: 0 };
  let aimTimer = null;
  const AIM_POST_MS = 100; // 10 Hz control cadence, independent of detection FPS

  // ------------------------------------------------------------- worker session
  function ensureWorker() {
    if (workerReady) return Promise.resolve();
    if (workerLoading) return workerLoading;
    if (cfg.model_available === false) return Promise.reject(new Error("model-missing"));

    // Versioned URL busts the (aggressive) worker cache after a code change.
    worker = new Worker(cfg.worker_url || "/static/ai-worker.js");
    workerLoading = new Promise((resolve, reject) => {
      worker.onmessage = (e) => {
        const m = e.data;
        if (m.type === "ready") { workerReady = true; resolve(); }
        else if (m.type === "info") { console.log("AI model:", m.outputName, "dims", m.dims, "(YOLOv8 = [1, 4+nc, N])"); }
        else if (m.type === "dets") {
          busy = false;
          if (mode === "yolo") {
            const dets = m.dets || [];
            handleDets(dets);
            // Live readout: best raw confidence the model saw this frame, so the
            // operator can tell "model sees it at 45% but conf is 70%" and tune ⚙.
            const pct = Math.round((m.maxScore || 0) * 100);
            aiBadge.textContent = `AI ${dets.length}·${pct}%`;
            aiBadge.className = "badge ai-on";
            // Full diagnostic once per second: reveals whether few detections are
            // caused by the confidence gate, the min-size gate, or the model itself.
            const now = performance.now();
            if (now - lastYoloLog > 1000) {
              lastYoloLog = now;
              console.log(`[AI] n=${dets.length} best=${pct}% | conf=${Math.round(conf * 100)}% min=${Math.round(minSize)}px vid=${lastVW}x${lastVH}`);
            }
          }
        }
        else if (m.type === "error") {
          busy = false;
          if (!workerReady) reject(new Error(m.message || "worker-error"));
          else console.error("AI worker error", m.message);
        }
      };
      worker.onerror = (err) => {
        busy = false;
        if (!workerReady) reject(err);
        else console.error("AI worker error", err);
      };
      worker.postMessage({ type: "init", modelUrl: cfg.model_url, imgsz: IMGSZ });
    }).finally(() => { workerLoading = null; });
    return workerLoading;
  }

  function loadClasses() {
    if (!cfg.classes_url) return;
    fetch(cfg.classes_url)
      .then((r) => (r.ok ? r.json() : {}))
      .then((j) => { classNames = j || {}; })
      .catch(() => {});
  }

  // -------------------------------------------------------------- toggles / HUD
  function setBadges() {
    aiBadge.textContent = mode === "off" ? "AI OFF" : (mode === "custom" ? "AI CUSTOM" : "AI ON");
    aiBadge.className = "badge" + (mode !== "off" ? " ai-on" : "");
    trackBadge.textContent = trackOn ? "TRACK ON" : "TRACK OFF";
    trackBadge.className = "badge" + (trackOn ? " track-on" : "");
    // Crosshair-side squares: AI = grey+hand when off, green "AI"/"AI+" otherwise;
    // Track = grey/green via .off.
    if (aiBox) {
      aiBox.classList.toggle("off", mode === "off");
      if (aiBoxLabel) aiBoxLabel.textContent = mode === "custom" ? "AI+" : "AI";
    }
    if (trackBox) trackBox.classList.toggle("off", !trackOn);
  }

  // I key: cycle OFF -> yolo -> custom -> OFF.
  async function toggle() {
    if (mode === "off") {
      aiBadge.textContent = "AI …";
      try {
        await ensureWorker();
      } catch (err) {
        aiBadge.textContent = err && err.message === "model-missing" ? "AI NO MODEL" : "AI ERROR";
        aiBadge.className = "badge armed";
        console.error("AI init failed", err);
        return;
      }
      mode = "yolo";
      setBadges();
      startLoop();
    } else if (mode === "yolo") {
      mode = "custom";
      prevPixels = null;
      clearCanvas();
      setBadges();
      if (!running) startLoop();
    } else {
      stop();
    }
  }

  function stop() {
    mode = "off";
    if (trackOn) toggleTrack(); // releases the turret aim + stops the timer
    running = false;
    prevPixels = null;
    clearCanvas();
    setBadges();
  }

  function toggleTrack() {
    if (mode === "off" && !trackOn) return; // T does nothing outside AI mode
    trackOn = !trackOn && mode !== "off";
    if (trackOn) {
      startAimTimer();
    } else {
      locked = null;
      currentAim = { active: false, rot: 0, ele: 0 };
      stopAimTimer();
      sendTrack(false, 0, 0); // release the turret
    }
    setBadges();
  }

  // Fixed-rate control loop: re-POST the latest aim so tracking is smooth and the
  // server aim never expires between (possibly slow) detection frames.
  function startAimTimer() {
    if (aimTimer) return;
    aimTimer = setInterval(() => {
      if (!trackOn) return;
      sendTrack(currentAim.active, currentAim.rot, currentAim.ele);
    }, AIM_POST_MS);
  }
  function stopAimTimer() {
    if (aimTimer) { clearInterval(aimTimer); aimTimer = null; }
  }

  function onCameraSwitch() {
    // Different camera -> any locked target / motion baseline is meaningless.
    locked = null;
    prevPixels = null;
    currentAim = { active: false, rot: 0, ele: 0 };
  }

  // ---------------------------------------------------------------- detect loop
  function startLoop() {
    if (running) return;
    running = true;
    const step = () => {
      if (mode === "off") { running = false; return; }
      if (mode === "yolo") {
        submitFrame();
      } else if (mode === "custom") {
        const t = performance.now();
        if (t - lastMotionAt >= MOTION_MS) { lastMotionAt = t; detectMotion(); }
      }
      requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  // --- YOLO: capture (main thread) -> worker inference. Gated on `busy` so only
  //     one frame is in flight, throttling to the worker's inference rate.
  function submitFrame() {
    if (busy || !workerReady) return;
    const video = window.cockpit && window.cockpit.videoEl;
    if (!video) return;
    const vw = video.videoWidth, vh = video.videoHeight;
    if (!vw || !vh) return;
    lastVW = vw; lastVH = vh;

    const r = Math.min(IMGSZ / vw, IMGSZ / vh);
    const newW = vw * r, newH = vh * r;
    const padX = (IMGSZ - newW) / 2, padY = (IMGSZ - newH) / 2;
    preCtx.fillStyle = "#000"; // BLACK padding — THIS model detects correctly only with black; grey degrades it badly
    preCtx.fillRect(0, 0, IMGSZ, IMGSZ);
    try {
      preCtx.drawImage(video, padX, padY, newW, newH);
    } catch (_) {
      return; // frame not decodable yet
    }
    const imgData = preCtx.getImageData(0, 0, IMGSZ, IMGSZ);
    busy = true;
    worker.postMessage(
      { type: "frame", px: imgData.data.buffer, padX, padY, newW, newH, vw, vh, conf, minSize },
      [imgData.data.buffer]
    );
  }

  // --- Custom: pixel motion detection on a downscaled frame (main thread, cheap).
  function detectMotion() {
    const video = window.cockpit && window.cockpit.videoEl;
    if (!video) return;
    const vw = video.videoWidth, vh = video.videoHeight;
    if (!vw || !vh) return;
    lastVW = vw; lastVH = vh;

    const DW = MOTION_W;
    const DH = Math.max(1, Math.round(DW * vh / vw));
    if (motionCanvas.width !== DW || motionCanvas.height !== DH) {
      motionCanvas.width = DW; motionCanvas.height = DH; prevPixels = null;
    }
    try { mctx.drawImage(video, 0, 0, DW, DH); }
    catch (_) { return; }
    const cur = mctx.getImageData(0, 0, DW, DH).data;
    const n = DW * DH;

    // Luminance projection profiles (used to estimate the camera's global shift).
    const curCol = new Float32Array(DW), curRow = new Float32Array(DH);
    for (let y = 0, i = 0; y < DH; y++) {
      for (let x = 0; x < DW; x++, i += 4) {
        const l = cur[i] * 0.299 + cur[i + 1] * 0.587 + cur[i + 2] * 0.114;
        curCol[x] += l; curRow[y] += l;
      }
    }

    if (!prevPixels || prevPixels.length !== cur.length) {
      prevPixels = cur; prevCol = curCol; prevRow = curRow; // seed baseline
      handleDets([]);
      return;
    }

    // Estimate the EGO-MOTION (camera pan/tilt ≈ a global image translation) by
    // 1D-correlating the profiles: cur[i] ≈ prev[i + s]. Align the previous frame
    // by this shift before diffing so the moving BACKGROUND cancels and only
    // objects that moved independently of the camera survive.
    const R = Math.round(maxShift);
    const sx = R > 0 ? estShift(curCol, prevCol, DW, R) : 0;
    const sy = R > 0 ? estShift(curRow, prevRow, DH, R) : 0;

    const mask = new Uint8Array(n);
    const thr = motionThresh / 100;
    let count = 0;
    for (let y = 0; y < DH; y++) {
      const py = y + sy;
      if (py < 0 || py >= DH) continue;
      for (let x = 0; x < DW; x++) {
        const px = x + sx;
        if (px < 0 || px >= DW) continue;
        const ci = (y * DW + x) * 4, pi = (py * DW + px) * 4;
        const d = (Math.abs(cur[ci] - prevPixels[pi]) +
                   Math.abs(cur[ci + 1] - prevPixels[pi + 1]) +
                   Math.abs(cur[ci + 2] - prevPixels[pi + 2])) / 765; // 3 * 255
        if (d > thr) { mask[y * DW + x] = 1; count++; }
      }
    }
    prevPixels = cur; prevCol = curCol; prevRow = curRow;

    // Guard: if a huge fraction still "moves", ego-motion wasn't compensated
    // (too-fast slew or a lighting change) — treat the frame as unreliable.
    if (count > n * GLOBAL_MOTION_FRAC) { handleDets([]); return; }

    // Dilate to bridge the edge-only fragments a frame diff produces, then
    // cluster into blobs and keep those big enough (min object size, px).
    const grown = dilate(mask, DW, DH, 2);
    const blobs = connectedComponents(grown, DW, DH);
    const dets = [];
    for (const b of blobs) {
      if (b.area < 4) continue; // reject speckle noise
      const wN = (b.maxX - b.minX + 1) / DW;
      const hN = (b.maxY - b.minY + 1) / DH;
      const sizePx = Math.max(wN * vw, hN * vh);
      if (sizePx < minSize) continue;
      const xN = ((b.minX + b.maxX + 1) / 2) / DW;
      const yN = ((b.minY + b.maxY + 1) / 2) / DH;
      dets.push({ x: xN, y: yN, w: wN, h: hN, score: 1, cls: "рух" });
    }
    handleDets(dets);
  }

  // Best integer shift s (in [-R, R]) that aligns cur to prev, i.e. minimises the
  // mean absolute difference of cur[i] vs prev[i + s] over their overlap. Used
  // per-axis on the luminance profiles to estimate the camera's global motion.
  function estShift(cur, prev, len, R) {
    let best = 0, bestErr = Infinity;
    for (let s = -R; s <= R; s++) {
      const start = Math.max(0, -s), end = Math.min(len, len - s);
      if (end <= start) continue;
      let err = 0;
      for (let i = start; i < end; i++) {
        const d = cur[i] - prev[i + s];
        err += d < 0 ? -d : d;
      }
      err /= (end - start);
      if (err < bestErr) { bestErr = err; best = s; }
    }
    return best;
  }

  function dilate(mask, DW, DH, R) {
    const out = new Uint8Array(DW * DH);
    for (let y = 0; y < DH; y++) {
      for (let x = 0; x < DW; x++) {
        if (!mask[y * DW + x]) continue;
        for (let dy = -R; dy <= R; dy++) {
          const yy = y + dy;
          if (yy < 0 || yy >= DH) continue;
          for (let dx = -R; dx <= R; dx++) {
            const xx = x + dx;
            if (xx < 0 || xx >= DW) continue;
            out[yy * DW + xx] = 1;
          }
        }
      }
    }
    return out;
  }

  function connectedComponents(mask, DW, DH) {
    const seen = new Uint8Array(DW * DH);
    const blobs = [];
    const stack = [];
    for (let s = 0; s < DW * DH; s++) {
      if (!mask[s] || seen[s]) continue;
      let minX = DW, minY = DH, maxX = 0, maxY = 0, area = 0;
      stack.length = 0; stack.push(s); seen[s] = 1;
      while (stack.length) {
        const p = stack.pop();
        const x = p % DW, y = (p - x) / DW;
        area++;
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
        if (x > 0)      { const q = p - 1;  if (mask[q] && !seen[q]) { seen[q] = 1; stack.push(q); } }
        if (x < DW - 1) { const q = p + 1;  if (mask[q] && !seen[q]) { seen[q] = 1; stack.push(q); } }
        if (y > 0)      { const q = p - DW; if (mask[q] && !seen[q]) { seen[q] = 1; stack.push(q); } }
        if (y < DH - 1) { const q = p + DW; if (mask[q] && !seen[q]) { seen[q] = 1; stack.push(q); } }
      }
      blobs.push({ minX, minY, maxX, maxY, area });
    }
    return blobs;
  }

  function handleDets(dets) {
    if (mode === "off") return;
    resizeCanvas();
    draw(dets, lastVW, lastVH);
    if (trackOn) updateTracking(dets);
  }

  // ----------------------------------------------- coordinate mapping (F <-> V)
  // Frame-normalised (fx, fy in 0..1 over the intrinsic video frame) <-> viewport
  // pixels. cockpit.js owns the video transform (object-fit: cover scale, digital
  // zoom, and — on the wide camera — the pan that pins the crosshair to the screen
  // centre) and publishes it as window.cockpit.view; read it rather than
  // re-deriving it, so the overlay can never disagree with the picture.
  function frameToViewport(fx, fy, vw, vh) {
    const v = (window.cockpit && window.cockpit.view) || { scale: 1, tx: 0, ty: 0 };
    return {
      x: window.innerWidth / 2 + v.tx + (fx - 0.5) * vw * v.scale,
      y: window.innerHeight / 2 + v.ty + (fy - 0.5) * vh * v.scale,
    };
  }

  // Invert the mapping at the reticle: the frame point the turret must centre on.
  // This is the aim target — on the wide camera it is the calibrated boresight and
  // does NOT move with zoom.
  function crosshairFrame(vw, vh) {
    const v = window.cockpit && window.cockpit.view;
    if (!v || !vw || !vh) return { x: 0.5, y: 0.5 };
    return {
      x: 0.5 + (v.crossX - window.innerWidth / 2 - v.tx) / (vw * v.scale),
      y: 0.5 + (v.crossY - window.innerHeight / 2 - v.ty) / (vh * v.scale),
    };
  }

  // ------------------------------------------------------------------- drawing
  function resizeCanvas() {
    const W = window.innerWidth, H = window.innerHeight;
    if (canvas.width !== W || canvas.height !== H) {
      canvas.width = W;
      canvas.height = H;
    }
  }

  function clearCanvas() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
  }

  function draw(dets, vw, vh) {
    clearCanvas();
    ctx.lineWidth = 2;
    ctx.font = "13px system-ui, sans-serif";
    ctx.textBaseline = "bottom";
    for (const det of dets) {
      const tl = frameToViewport(det.x - det.w / 2, det.y - det.h / 2, vw, vh);
      const br = frameToViewport(det.x + det.w / 2, det.y + det.h / 2, vw, vh);
      const isLocked = locked && locked.det === det;
      const color = isLocked ? "#ff3b3b" : "#38ff9e";
      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      ctx.strokeRect(tl.x, tl.y, br.x - tl.x, br.y - tl.y);
      const name = typeof det.cls === "string"
        ? det.cls
        : (classNames[det.cls] || classNames[String(det.cls)] || "target");
      const label = `${name} ${Math.round(det.score * 100)}%`;
      ctx.fillText(label, tl.x + 2, tl.y - 2);
    }
  }

  // -------------------------------------------------------- tracking (servo)
  function updateTracking(dets) {
    const video = window.cockpit && window.cockpit.videoEl;
    if (!video) return;
    const vw = video.videoWidth, vh = video.videoHeight;

    if (!dets.length) {
      // No target visible: RELEASE the aim override so manual WASD works again.
      // trackOn stays true, so the servo re-acquires the moment a target appears.
      locked = null;
      currentAim = { active: false, rot: 0, ele: 0 };
      return;
    }

    const aim = crosshairFrame(vw, vh);
    // Acquire: nearest to the crosshair. Maintain: nearest to the previous lock.
    const ref = locked ? locked : { x: aim.x, y: aim.y };
    let target = dets[0], bestD = Infinity;
    for (const det of dets) {
      const dx = det.x - ref.x, dy = det.y - ref.y;
      const dist = dx * dx + dy * dy;
      if (dist < bestD) { bestD = dist; target = det; }
    }
    locked = { x: target.x, y: target.y, det: target };

    // Proportional visual servo on the pixel error to the crosshair.
    let errX = target.x - aim.x;
    let errY = target.y - aim.y;
    if (Math.abs(errX) < DEADZONE) errX = 0;
    if (Math.abs(errY) < DEADZONE) errY = 0;
    const rot = clamp(GAIN * errX, MAX_VEL);    // target right of crosshair -> pan right
    const ele = clamp(-GAIN * errY, MAX_VEL);   // target below crosshair -> tilt down
    currentAim = { active: true, rot, ele };
  }

  function clamp(v, m) { return Math.max(-m, Math.min(m, v)); }

  function sendTrack(active, rot, ele) {
    fetch("/api/track", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ active, rot, ele }),
      keepalive: true,
    }).catch(() => {});
  }

  // ------------------------------------------------------ ⚙ detection settings
  const confEl = document.getElementById("ai-conf");
  const confVal = document.getElementById("ai-conf-val");
  const minEl = document.getElementById("ai-minsize");
  const minVal = document.getElementById("ai-minsize-val");
  const motionEl = document.getElementById("ai-motion");
  const motionVal = document.getElementById("ai-motion-val");
  const shiftEl = document.getElementById("ai-maxshift");
  const shiftVal = document.getElementById("ai-maxshift-val");

  function initSettingsUI() {
    if (confEl) { confEl.value = Math.round(conf * 100); confVal.textContent = Math.round(conf * 100); }
    if (minEl) { minEl.value = Math.round(minSize); minVal.textContent = Math.round(minSize); }
    if (motionEl) { motionEl.value = Math.round(motionThresh); motionVal.textContent = Math.round(motionThresh); }
    if (shiftEl) { shiftEl.value = Math.round(maxShift); shiftVal.textContent = Math.round(maxShift); }
  }

  let saveTimer = null;
  function saveSettings() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      fetch("/api/ai-settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conf, min_size: minSize, motion_thresh: motionThresh, max_shift: maxShift }),
      }).catch(() => {});
    }, 250);
  }

  if (confEl) {
    confEl.addEventListener("input", () => {
      conf = (parseFloat(confEl.value) || 70) / 100;
      confVal.textContent = Math.round(conf * 100);
      saveSettings();
    });
  }
  if (minEl) {
    minEl.addEventListener("input", () => {
      minSize = parseFloat(minEl.value) || 0;
      minVal.textContent = Math.round(minSize);
      saveSettings();
    });
  }
  if (motionEl) {
    motionEl.addEventListener("input", () => {
      motionThresh = parseFloat(motionEl.value) || 1;
      motionVal.textContent = Math.round(motionThresh);
      saveSettings();
    });
  }
  if (shiftEl) {
    shiftEl.addEventListener("input", () => {
      maxShift = parseFloat(shiftEl.value) || 0;
      shiftVal.textContent = Math.round(maxShift);
      saveSettings();
    });
  }

  // ------------------------------------------------------------------- init
  loadClasses();
  initSettingsUI();
  setBadges();

  return { toggle, toggleTrack, onCameraSwitch, isOn: () => mode !== "off" };
})();

window.AI = AI;
