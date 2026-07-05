import { OverlayUI } from "../ui/overlay";
import { SidePanel } from "../ui/side-panel";
import { VirtualKeysInput } from "../input/virtual-keys";

export type Layout = {
  root: HTMLElement;
  videoContainer: HTMLElement;
  overlay: OverlayUI;
  sidePanel: SidePanel;
  virtualKeys: VirtualKeysInput;
};

export function initLayout(): Layout {
  const root = document.getElementById("app")!;

  // z-index 0: video
  const videoContainer = document.createElement("div");
  videoContainer.className = "video-layer";
  root.appendChild(videoContainer);

  // z-index 10: overlay HUD (pointer-events: none)
  const overlay = new OverlayUI(root);

  // z-index 15: virtual keys
  const virtualKeys = new VirtualKeysInput(root);

  // z-index 20: side panel
  const sidePanel = new SidePanel(root);

  return { root, videoContainer, overlay, sidePanel, virtualKeys };
}
