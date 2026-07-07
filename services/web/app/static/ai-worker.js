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

importScripts("/static/vendor/ort.min.js");

let session = null;
let imgsz = 640;
let reported = false;
const IOU_THRESHOLD = 0.45;

self.onmessage = async (e) => {
  const m = e.data;

  if (m.type === "init") {
    imgsz = m.imgsz || 640;
    // Single-threaded SIMD WASM: no COOP/COEP requirement, fast enough here.
    if (self.ort && ort.env && ort.env.wasm) {
      ort.env.wasm.wasmPaths = "/static/vendor/";
      ort.env.wasm.numThreads = 1;
      ort.env.wasm.simd = true;
    }
    try {
      session = await ort.InferenceSession.create(m.modelUrl, { executionProviders: ["wasm"] });
      self.postMessage({ type: "ready" });
    } catch (err) {
      self.postMessage({ type: "error", message: String((err && err.message) || err) });
    }
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
    self.postMessage({ type: "dets", dets, maxScore });
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
