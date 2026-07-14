"use strict";

// =============================================================================
// YOLO inference Web Worker. Runs OFF the main thread so browser-side detection
// never starves the cockpit's input/heartbeat timers (which keep the 20 Hz UDP
// command stream and its 400 ms deadman alive). The main thread captures the
// frame pixels EXACTLY as the original working version did (2D canvas drawImage
// + black letterbox + getImageData) and transfers the raw RGBA buffer here; the
// worker only builds the tensor, runs ONNX inference, decodes and NMSes. Keeping
// the pixel path identical to the proven main-thread version preserves detection
// quality; only the heavy compute is offloaded.
// =============================================================================

// ORT's WebGPU build is an ES module (since 1.18), so this is a MODULE worker and
// imports it rather than importScripts()-ing a classic bundle. The "bundle" variant
// is self-contained: WebGPU + WASM execution providers, no extra loader fetch.
//
// Vendored by scripts/fetch_ort.sh. Do NOT downgrade ORT: 1.17 calls
// adapter.requestAdapterInfo(), long removed from the WebGPU spec, so its GPU
// backend throws on any current browser and inference falls back to the slow CPU.
import * as ort from "/static/vendor/ort.webgpu.bundle.min.mjs";

let session = null;
let imgsz = 640;
let reported = false;
const IOU_THRESHOLD = 0.45;

// Which backend actually came up, and — when it is the slow one — why. Reported to
// the ⚙ panel: on WASM a YOLO11s frame costs ~500 ms (≈2 FPS), on WebGPU ~20-40 ms,
// so the operator must be able to SEE which one they got rather than guess.
let backend = "wasm";
let backendNote = "";

// WebGPU is gated on a secure context: navigator.gpu simply does not exist on a
// plain http:// LAN origin, which is exactly how the cockpit is normally served.
// Name that case explicitly — it is the difference between "your GPU is unsupported"
// (nothing to do) and "serve this over HTTPS/localhost" (a fixable config).
function webgpuBlockedReason() {
  if (typeof navigator === "undefined" || !navigator.gpu) {
    return self.isSecureContext
      ? "браузер не підтримує WebGPU"
      : "потрібен захищений контекст (HTTPS або localhost)";
  }
  return "";
}

self.onmessage = async (e) => {
  const m = e.data;

  if (m.type === "init") {
    imgsz = m.imgsz || 640;
    // Serve the .wasm from our own vendor dir (no CDN), single-threaded: WASM
    // threads would need COOP/COEP cross-origin isolation, which the cockpit does
    // not set. This binary is both the WebGPU backend's dependency and the CPU
    // fallback.
    ort.env.wasm.wasmPaths = "/static/vendor/";
    ort.env.wasm.numThreads = 1;

    backend = "wasm";
    backendNote = webgpuBlockedReason();
    session = null;

    if (!backendNote) {
      try {
        session = await ort.InferenceSession.create(m.modelUrl, { executionProviders: ["webgpu"] });
        backend = "webgpu";
      } catch (err) {
        // A GPU that reports itself but cannot run the graph (unsupported op,
        // driver refusal): keep AI alive on WASM rather than failing the mode.
        backendNote = "WebGPU не піднявся: " + String((err && err.message) || err);
        session = null;
      }
    }

    if (!session) {
      try {
        session = await ort.InferenceSession.create(m.modelUrl, { executionProviders: ["wasm"] });
      } catch (err) {
        self.postMessage({ type: "error", message: String((err && err.message) || err) });
        return;
      }
    }
    self.postMessage({ type: "ready", backend, note: backendNote });
    return;
  }

  if (m.type === "frame") {
    if (!session) { self.postMessage({ type: "dets", dets: [] }); return; }
    // px: raw RGBA (imgsz*imgsz*4) letterboxed frame captured on the main thread.
    const { px, padX, padY, newW, newH, vw, vh, conf, minSize } = m;
    const rgba = new Uint8ClampedArray(px);

    // HWC uint8 RGBA -> CHW float32 RGB in [0, 1].
    const area = imgsz * imgsz;
    const input = new Float32Array(area * 3);
    for (let i = 0; i < area; i++) {
      input[i] = rgba[i * 4] / 255;
      input[i + area] = rgba[i * 4 + 1] / 255;
      input[i + 2 * area] = rgba[i * 4 + 2] / 255;
    }
    const tensor = new ort.Tensor("float32", input, [1, 3, imgsz, imgsz]);

    let output;
    const startedAt = performance.now();
    try {
      const feeds = {};
      feeds[session.inputNames[0]] = tensor;
      const out = await session.run(feeds);
      output = out[session.outputNames[0]];
    } catch (err) {
      self.postMessage({ type: "error", message: String((err && err.message) || err) });
      return;
    }

    const { dets, maxScore } = decode(output, { padX, padY, newW, newH, vw, vh, conf, minSize });
    if (!reported) {
      reported = true;
      // One-time diagnostic: the real output shape reveals the head layout
      // (YOLOv8 = [1, 4+nc, N]); useful if detection quality looks wrong.
      self.postMessage({ type: "info", dims: Array.from(output.dims), inputName: session.inputNames[0], outputName: session.outputNames[0] });
    }
    // ms: inference time, shown in the ⚙ panel's engine readout.
    self.postMessage({ type: "dets", dets, maxScore, ms: Math.round(performance.now() - startedAt) });
  }
};

// Decode a YOLOv8 head [1, 4+nc, N] (or transposed) into frame-normalised boxes.
function decode(output, lb) {
  const data = output.data;
  const d = output.dims; // [1, a, b]
  const a = d[1], b = d[2];
  const channelsFirst = a <= b; // channel axis (4+nc) is the smaller one
  const C = channelsFirst ? a : b;
  const N = channelsFirst ? b : a;
  const nc = C - 4;
  const at = channelsFirst ? (c, i) => data[c * N + i] : (c, i) => data[i * C + c];

  const cand = [];
  let maxScore = 0; // best raw confidence across ALL anchors (pre-threshold) for tuning
  for (let i = 0; i < N; i++) {
    let best = 0, bestCls = 0;
    for (let c = 0; c < nc; c++) {
      const s = at(4 + c, i);
      if (s > best) { best = s; bestCls = c; }
    }
    if (best > maxScore) maxScore = best;
    if (best < lb.conf) continue;

    // Box centre/size in model-input px -> frame-normalised (undo letterbox).
    const cx = at(0, i), cy = at(1, i), w = at(2, i), h = at(3, i);
    const fx = (cx - lb.padX) / lb.newW;
    const fy = (cy - lb.padY) / lb.newH;
    const fw = w / lb.newW;
    const fh = h / lb.newH;

    // Minimum-size gate: longer box side in SOURCE-FRAME pixels.
    const sizePx = Math.max(fw * lb.vw, fh * lb.vh);
    if (sizePx < lb.minSize) continue;

    cand.push({ x: fx, y: fy, w: fw, h: fh, score: best, cls: bestCls });
  }
  return { dets: nms(cand, IOU_THRESHOLD), maxScore };
}

function nms(boxes, iouThr) {
  boxes.sort((p, q) => q.score - p.score);
  const keep = [];
  for (const box of boxes) {
    let drop = false;
    for (const k of keep) {
      if (iou(box, k) > iouThr) { drop = true; break; }
    }
    if (!drop) keep.push(box);
  }
  return keep;
}

function iou(p, q) {
  const px1 = p.x - p.w / 2, py1 = p.y - p.h / 2, px2 = p.x + p.w / 2, py2 = p.y + p.h / 2;
  const qx1 = q.x - q.w / 2, qy1 = q.y - q.h / 2, qx2 = q.x + q.w / 2, qy2 = q.y + q.h / 2;
  const ix = Math.max(0, Math.min(px2, qx2) - Math.max(px1, qx1));
  const iy = Math.max(0, Math.min(py2, qy2) - Math.max(py1, qy1));
  const inter = ix * iy;
  const union = p.w * p.h + q.w * q.h - inter;
  return union > 0 ? inter / union : 0;
}
