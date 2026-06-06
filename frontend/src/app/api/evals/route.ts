import { NextResponse } from "next/server";

import { fetchBackend } from "@/lib/server-api";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    // /evals lands in Phase 7. Until then the backend returns 404 and we
    // surface the empty shape so the dashboard renders cleanly.
    const data = await fetchBackend<unknown>("/evals").catch(() => ({
      latest: null,
      history: [],
    }));
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json(
      { error: (err as Error).message },
      { status: 502 },
    );
  }
}
