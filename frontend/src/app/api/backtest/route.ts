import { NextResponse } from "next/server";

import { fetchBackend } from "@/lib/server-api";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const list = await fetchBackend<{ id: number }[]>("/backtest");
    if (list.length === 0) return NextResponse.json({ detail: null });
    const latest = await fetchBackend<unknown>(`/backtest/${list[0].id}`);
    return NextResponse.json({ detail: latest });
  } catch (err) {
    return NextResponse.json(
      { error: (err as Error).message },
      { status: 502 },
    );
  }
}
