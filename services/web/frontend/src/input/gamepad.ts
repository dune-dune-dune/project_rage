import type { JoystickState } from "../types/input";

export type { JoystickState };

export class GamepadInput {
  private index: number | null = null;

  constructor() {
    window.addEventListener("gamepadconnected", (e: GamepadEvent) => {
      this.index = e.gamepad.index;
      console.log(`[Gamepad] Connected at index ${this.index}`);
    });

    window.addEventListener("gamepaddisconnected", () => {
      console.log("[Gamepad] Disconnected");
      this.index = null;
    });

    setInterval(() => {
      const pads = navigator.getGamepads();
      if (!pads) return;
      for (let i = 0; i < pads.length; i++) {
        if (pads[i] && this.index === null) {
          this.index = i;
          console.log(`[Gamepad] Auto-detected at index ${i}`);
          break;
        }
      }
    }, 500);
  }

  getState(): JoystickState | null {
    if (navigator.getGamepads === undefined) return null;

    const pads = navigator.getGamepads();
    if (!pads) return null;

    let gp = this.index !== null ? pads[this.index] : null;
    if (!gp) {
      for (let i = 0; i < pads.length; i++) {
        if (pads[i]) { gp = pads[i]; break; }
      }
    }
    if (!gp) return null;

    const x = this.getAxis(gp, 0);
    const y = -this.getAxis(gp, 1);

    let buttons = 0;
    if (this.isPressed(gp, 0)) buttons |= 0x01;  // A → fire
    if (this.isPressed(gp, 4)) buttons |= 0x02;  // LB → slow
    if (this.isPressed(gp, 2)) buttons |= 0x04;  // X → reload
    if (this.isPressed(gp, 3)) buttons |= 0x08;  // Y → arm
    if (this.isPressed(gp, 5)) buttons |= 0x10;  // RB → force_home

    return { x, y, buttons };
  }

  private getAxis(gp: Gamepad, index: number): number {
    if (!gp.axes || index >= gp.axes.length) return 0;
    const v = gp.axes[index];
    return Math.abs(v) < 0.15 ? 0 : Math.max(-1, Math.min(1, v));
  }

  private isPressed(gp: Gamepad, index: number): boolean {
    if (!gp.buttons || index >= gp.buttons.length) return false;
    return gp.buttons[index].pressed;
  }
}
