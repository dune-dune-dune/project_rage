export type RuntimeConfig = {
  whepUrl?: string;
  wtUrl?: string;
  /** Base64 SHA-256 of the server's DER certificate, for serverCertificateHashes. */
  certHash?: string;
  debug?: boolean;
};

let cached: RuntimeConfig | null = null;

export async function loadConfig(): Promise<RuntimeConfig> {
  if (cached) return cached;

  try {
    const res = await fetch("/config.json");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const body = await res.json();
    cached = (body != null && typeof body === "object" ? body : {}) as RuntimeConfig;
  } catch (e) {
    console.warn("[Config] /config.json unavailable, using env fallback:", e instanceof Error ? e.message : String(e));
    cached = {
      // whepUrl intentionally absent: must come from server config
    //   whepUrl: import.meta.env.VITE_WHEP_URL || undefined,
      wtUrl: import.meta.env.VITE_WT_URL || undefined,
      debug: true,
    };
  }

  return cached;
}

export function getConfig(): RuntimeConfig {
  return cached ?? {};
}
