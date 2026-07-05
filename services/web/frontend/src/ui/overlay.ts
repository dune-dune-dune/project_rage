export class OverlayUI {
  private el: HTMLDivElement;

  constructor(parent: HTMLElement) {
    this.el = document.createElement("div");
    this.el.className = "overlay-layer";
    parent.appendChild(this.el);
  }

  mount(node: HTMLElement): void {
    this.el.appendChild(node);
  }
}
