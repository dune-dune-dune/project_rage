import type { JoystickState } from "../types/input";

export class KeyboardInput {
  private keys: Set<string> = new Set();

  constructor() {
    window.addEventListener("keydown", (e) => {
      this.keys.add(e.key.toLowerCase());
    });
    window.addEventListener("keyup", (e) => {
      this.keys.delete(e.key.toLowerCase());
    });
    window.addEventListener("blur", () => {
      this.keys.clear();
    });
  }

  getState(): JoystickState {
    let x = 0;
    let y = 0;
    let buttons = 0;

    if (this.keys.has("w")) y = 1;
    if (this.keys.has("s")) y = -1;
    if (this.keys.has("a")) x = -1;
    if (this.keys.has("d")) x = 1;

    if (this.keys.has("control")) buttons |= 0x01;  // fire
    if (this.keys.has("shift"))   buttons |= 0x02;  // slow
    if (this.keys.has("r"))       buttons |= 0x04;  // reload
    if (this.keys.has(" "))       buttons |= 0x08;  // arm
    if (this.keys.has("h"))       buttons |= 0x10;  // force_home

    return { x, y, buttons };
  }
}
