import "./style.css";
import { loadConfig } from "./config/runtime";
import { initLayout } from "./core/layout";
import { startApp } from "./core/loop";
import { initVideo } from "./video/webrtc";

function showNoSignal(container: HTMLElement): void {
  const el = document.createElement("div");
  el.className = "no-signal";
  el.textContent = "NO SIGNAL";
  container.appendChild(el);
}

async function boot(): Promise<void> {
  try {
    const cfg = await loadConfig();
    const layout = initLayout();

    startApp(cfg, layout.virtualKeys).catch((err) =>
      console.error("[Boot] Loop error:", err),
    );

    if (cfg.whepUrl) {
      initVideo(layout.videoContainer, cfg.whepUrl).catch((err) => {
        console.warn("[Boot] Video failed:", err);
        showNoSignal(layout.videoContainer);
      });
    } else {
      showNoSignal(layout.videoContainer);
    }
  } catch (err) {
    const root = document.getElementById("app");
    if (root) {
      root.innerHTML = `<div style="color:red;padding:24px;font-family:monospace">
        <b>Boot failed</b><br><pre>${err instanceof Error ? err.message : String(err)}</pre>
      </div>`;
    }
  }
}

boot();
