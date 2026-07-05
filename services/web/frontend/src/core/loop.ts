import type { JoystickState } from "../types/input";
import type { RuntimeConfig } from "../config/runtime";
import { GamepadInput } from "../input/gamepad";
import { KeyboardInput } from "../input/keyboard";
import type { VirtualKeysInput } from "../input/virtual-keys";
import { WebTransportClient } from "../transport/webtransport";

const TARGET_HZ = 120;
const FRAME_MS = 1000 / TARGET_HZ;
const RETRY_MS = 3000;

function merge(
  gamepad: JoystickState | null,
  kb: JoystickState,
  vk: JoystickState,
): JoystickState {
  const x = kb.x !== 0 ? kb.x : vk.x !== 0 ? vk.x : (gamepad?.x ?? 0);
  const y = kb.y !== 0 ? kb.y : vk.y !== 0 ? vk.y : (gamepad?.y ?? 0);
  const buttons = (gamepad?.buttons ?? 0) | kb.buttons | vk.buttons;
  return { x, y, buttons };
}

export async function startApp(cfg: RuntimeConfig, vKeys: VirtualKeysInput): Promise<void> {
  const gamepad = new GamepadInput();
  const keyboard = new KeyboardInput();
  const transport = new WebTransportClient();
  let stopping = false;

  transport.onError((err) => {
    console.error(`[Transport] ${err.code}: ${err.message}`);
  });

  window.addEventListener("beforeunload", () => {
    stopping = true;
    transport.close();
  });

  async function ensureConnected(): Promise<void> {
    if (transport.isConnected()) return;
    try {
      const url = cfg.wtUrl ?? import.meta.env.VITE_WT_URL ?? "https://localhost:4433/";
      await transport.connect(url, cfg.certHash);
    } catch {
      await new Promise((r) => setTimeout(r, RETRY_MS));
      ensureConnected().catch(() => undefined);
    }
  }

  ensureConnected();

  function tick(): void {
    if (stopping) return;

    const state = merge(gamepad.getState(), keyboard.getState(), vKeys.getState());

    if (transport.isConnected()) {
      transport.sendJoystick(state.x, state.y, state.buttons);
    }

    setTimeout(tick, FRAME_MS);
  }

  tick();
}
