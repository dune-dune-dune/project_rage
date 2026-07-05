export type WebTransportError = {
  code: string;
  message: string;
};

export type WebTransportListener = (error: WebTransportError) => void;

export class WebTransportClient {
  private transport: WebTransport | null = null;
  private writer: WritableStreamDefaultWriter<Uint8Array> | null = null;
  private listeners: Set<WebTransportListener> = new Set();
  private _closing = false;

  async connect(url: string, certHash?: string): Promise<void> {
    if (this.transport) {
      throw new Error("Already connected");
    }

    try {
      const options: WebTransportOptions = {};
      if (certHash) {
        const binary = Uint8Array.from(atob(certHash), (c) => c.charCodeAt(0));
        options.serverCertificateHashes = [{ algorithm: "sha-256", value: binary.buffer }];
      }
      this.transport = new WebTransport(url, options);
      await this.transport.ready;
      this.writer = this.transport.datagrams.writable.getWriter();
      console.log(`[WebTransport] Connected to ${url}`);
    } catch (error) {
      this.transport = null;
      this.writer = null;
      const message = error instanceof Error ? error.message : String(error);
      this.notifyError({ code: "CONNECT_FAILED", message });
      throw error;
    }
  }

  sendJoystick(x: number, y: number, buttons: number): void {
    if (!this.writer || !this.transport || this._closing) {
      return;
    }

    const pkt = new Uint8Array(3);
    pkt[0] = buttons & 0xff;
    pkt[1] = Math.max(0, Math.min(255, ((x + 1) * 128) | 0));
    pkt[2] = Math.max(0, Math.min(255, ((y + 1) * 128) | 0));

    this.writer.write(pkt).catch((error: unknown) => {
      const message = error instanceof Error ? error.message : String(error);
      this.notifyError({ code: "SEND_FAILED", message });
    });
  }

  onError(listener: WebTransportListener): () => void {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  }

  private notifyError(error: WebTransportError): void {
    for (const listener of this.listeners) {
      try { listener(error); } catch { /* swallow listener errors */ }
    }
  }

  isConnected(): boolean {
    return this.transport !== null && this.writer !== null && !this._closing;
  }

  async close(): Promise<void> {
    this._closing = true;
    try {
      if (this.writer) await this.writer.close().catch(() => undefined);
      if (this.transport) this.transport.close();
    } finally {
      this.transport = null;
      this.writer = null;
      this._closing = false;
    }
  }
}
