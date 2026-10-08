import { NextResponse } from "next/server";

const tradeBrainUrl = process.env.TRADE_BRAIN_URL ?? "http://127.0.0.1:8090";

export async function proxyTradeBrain(
  path: string,
  unavailableMessage: string,
  init?: RequestInit,
): Promise<NextResponse> {
  try {
    const response = await fetch(`${tradeBrainUrl}${path}`, { ...init, cache: "no-store" });
    const payload: unknown = await response.json();
    return NextResponse.json(payload, { status: response.status });
  } catch {
    return NextResponse.json({ error: unavailableMessage }, { status: 503 });
  }
}
