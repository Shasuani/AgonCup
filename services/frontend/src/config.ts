// Runtime configuration sourced from Vite env vars (build-time inlined).
//
// NEVER put secrets here — anything referenced ends up in the public bundle.
// Only non-secret, public config (the backend origin) belongs in the SPA.

interface AppConfig {
  /** Backend origin, e.g. https://api.example.com (no trailing /api/v1). */
  apiBaseUrl: string;
}

function readApiBaseUrl(): string {
  // Vite exposes only VITE_*-prefixed vars to client code.
  const fromEnv = import.meta.env?.VITE_API_BASE_URL;
  if (typeof fromEnv === "string" && fromEnv.length > 0) {
    return fromEnv;
  }
  // Same-origin default: the SPA and API are served from the same host.
  return "";
}

export const config: AppConfig = {
  apiBaseUrl: readApiBaseUrl(),
};
