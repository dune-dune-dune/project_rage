const STORAGE_KEY = "rws_panel_open";

export class SidePanel {
  private panel: HTMLDivElement;
  private toggle: HTMLButtonElement;
  private open: boolean;

  constructor(parent: HTMLElement) {
    this.open = localStorage.getItem(STORAGE_KEY) !== "false";

    this.panel = document.createElement("div");
    this.panel.className = "side-panel";

    this.toggle = document.createElement("button");
    this.toggle.className = "side-panel-toggle";
    this.toggle.type = "button";
    this.toggle.addEventListener("click", () => this.setOpen(!this.open));

    this.panel.appendChild(this.toggle);
    this.panel.appendChild(this.buildContent());

    parent.appendChild(this.panel);
    this.apply();
  }

  private buildContent(): HTMLElement {
    const content = document.createElement("div");
    content.className = "side-panel-content";

    content.appendChild(this.section("Connection", [
      "Status: —",
      "WebTransport: —",
    ]));

    content.appendChild(this.section("Video", [
      "Source: —",
    ]));

    content.appendChild(this.section("Control", [
      "Owner: —",
      "Fire Modes: —",
    ]));

    return content;
  }

  private section(title: string, rows: string[]): HTMLElement {
    const el = document.createElement("div");
    el.className = "side-panel-section";
    const h = document.createElement("div");
    h.className = "side-panel-section-title";
    h.textContent = title;
    el.appendChild(h);
    rows.forEach((row) => {
      const r = document.createElement("div");
      r.className = "side-panel-row";
      r.textContent = row;
      el.appendChild(r);
    });
    return el;
  }

  private setOpen(open: boolean): void {
    this.open = open;
    this.apply();
  }

  private apply(): void {
    this.panel.classList.toggle("closed", !this.open);
    this.toggle.textContent = this.open ? "»" : "«";
    localStorage.setItem(STORAGE_KEY, String(this.open));
  }
}
