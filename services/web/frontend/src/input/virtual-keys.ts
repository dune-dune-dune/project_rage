import type { JoystickState } from "../types/input";

type DirKey = { dx: number; dy: number };
type ActionKey = { bit: number };

export class VirtualKeysInput {
  private x = 0;
  private y = 0;
  private buttons = 0;

  constructor(parent: HTMLElement) {
    const layer = document.createElement("div");
    layer.className = "vkeys-layer";

    layer.appendChild(this.buildDpad());
    layer.appendChild(this.buildActions());

    parent.appendChild(layer);
  }

  getState(): JoystickState {
    return { x: this.x, y: this.y, buttons: this.buttons };
  }

  private buildDpad(): HTMLElement {
    const grid = document.createElement("div");
    grid.className = "vkeys-dpad";

    const empty = () => document.createElement("span");

    const dirs: (DirKey | null)[] = [
      null,             { dx: 0,  dy: 1  }, null,
      { dx: -1, dy: 0 }, null,             { dx: 1, dy: 0 },
      null,             { dx: 0,  dy: -1 }, null,
    ];
    const labels = ["", "▲", "", "◄", "", "►", "", "▼", ""];

    dirs.forEach((dir, i) => {
      if (!dir) { grid.appendChild(empty()); return; }
      const btn = this.makeBtn(labels[i]);
      this.bindDir(btn, dir);
      grid.appendChild(btn);
    });

    return grid;
  }

  private buildActions(): HTMLElement {
    const grid = document.createElement("div");
    grid.className = "vkeys-actions";

    const items: { label: string; bit: number; cls?: string }[] = [
      { label: "SLOW",   bit: 0x02 },
      { label: "ARM",    bit: 0x08 },
      { label: "FIRE",   bit: 0x01, cls: "vkey-fire" },
      { label: "RELOAD", bit: 0x04 },
      { label: "HOME",   bit: 0x10 },
    ];

    items.forEach(({ label, bit, cls }) => {
      const btn = this.makeBtn(label, cls);
      this.bindAction(btn, { bit });
      grid.appendChild(btn);
    });

    // 6th cell padding
    grid.appendChild(document.createElement("span"));

    return grid;
  }

  private makeBtn(label: string, extra?: string): HTMLButtonElement {
    const btn = document.createElement("button");
    btn.className = extra ? `vkey ${extra}` : "vkey";
    btn.textContent = label;
    btn.type = "button";
    return btn;
  }

  private bindDir(btn: HTMLButtonElement, { dx, dy }: DirKey): void {
    const press = (e: PointerEvent) => {
      e.preventDefault();
      btn.setPointerCapture(e.pointerId);
      if (dx !== 0) this.x = dx;
      if (dy !== 0) this.y = dy;
      btn.classList.add("pressed");
    };
    const release = () => {
      if (dx !== 0 && this.x === dx) this.x = 0;
      if (dy !== 0 && this.y === dy) this.y = 0;
      btn.classList.remove("pressed");
    };
    btn.addEventListener("pointerdown", press);
    btn.addEventListener("pointerup", release);
    btn.addEventListener("pointercancel", release);
  }

  private bindAction(btn: HTMLButtonElement, { bit }: ActionKey): void {
    const press = (e: PointerEvent) => {
      e.preventDefault();
      btn.setPointerCapture(e.pointerId);
      this.buttons |= bit;
      btn.classList.add("pressed");
    };
    const release = () => {
      this.buttons &= ~bit;
      btn.classList.remove("pressed");
    };
    btn.addEventListener("pointerdown", press);
    btn.addEventListener("pointerup", release);
    btn.addEventListener("pointercancel", release);
  }
}
