/**
 * Server-only fetch wrapper for the FastAPI backend.
 *
 * The viewer API key lives in process.env.APP_API_KEY_VIEWER_SERVER and is
 * read ONLY in this file. CI greps for any other source file referencing
 * the key and fails the build if it finds one — this is the only place
 * the key is allowed to appear in the frontend tree.
 *
 * Anything that talks to FastAPI from the browser must go through a Next
 * Route Handler (src/app/api/*) which calls fetchBackend() server-side.
 */
import "server-only";
import { serverEnv } from "./env";

type FetchOpts = {
  method?: string;
  body?: unknown;
  headers?: Record<string, string>;
  requestId?: string;
};

export async function fetchBackend<T>(path: string, opts: FetchOpts = {}): Promise<T> {
  const base = serverEnv.backendApiUrl();
  const key = serverEnv.viewerApiKey();
  const url = `${base.replace(/\/$/, "")}${path}`;
  const headers: Record<string, string> = {
    "X-API-Key": key,
    "Content-Type": "application/json",
    ...(opts.headers ?? {}),
  };
  if (opts.requestId) headers["X-Request-ID"] = opts.requestId;

  const resp = await fetch(url, {
    method: opts.method ?? "GET",
    headers,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    cache: "no-store",
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`backend ${resp.status} on ${path}: ${text.slice(0, 200)}`);
  }
  return (await resp.json()) as T;
}
