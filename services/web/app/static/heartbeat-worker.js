"use strict";

// Dedicated worker that drives the control heartbeat cadence.
//
// A background browser tab throttles main-thread setInterval/setTimeout to
// >=1 s. That would starve the 400 ms server-side deadman: the cockpit stops
// looking "alive", the turret gets a neutral packet, ENABLE drops and the
// motors de-energise — so the turret sags off its aim point while the operator
// is on another tab. A dedicated worker's timers are NOT background-throttled,
// so it keeps ticking at the requested rate; the main thread POSTs the current
// (motion/fire-zeroed) intent on each tick, which just holds position.
let timer = null;

self.onmessage = (e) => {
  const msg = e.data || {};
  if (msg.type === "start") {
    if (timer !== null) clearInterval(timer);
    timer = setInterval(() => self.postMessage({ type: "tick" }), msg.intervalMs || 150);
  } else if (msg.type === "stop") {
    if (timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  }
};
