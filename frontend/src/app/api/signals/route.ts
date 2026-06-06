import { NextResponse } from "next/server";

import { fetchBackend } from "@/lib/server-api";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const data = await fetchBackend<unknown>("/signals");
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json(
      { error: (err as Error).message },
      { status: 502 },
    );
  }
}
