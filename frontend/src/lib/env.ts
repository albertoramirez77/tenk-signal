/**
 * Tiny runtime checker for env vars used in server components. Anything
 * with a NEXT_PUBLIC_ prefix is sent to the browser; the viewer API key
 * MUST NOT have that prefix. CI greps to enforce this.
 */
import "server-only";

function required(name: string): string {
  const v = process.env[name];
  if (!v) {
    throw new Error(`missing required env var: ${name}`);
  }
  return v;
}

export const serverEnv = {
  backendApiUrl: () => required("BACKEND_API_URL"),
  viewerApiKey: () => required("APP_API_KEY_VIEWER_SERVER"),
};
