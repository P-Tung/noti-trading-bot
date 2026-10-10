import { proxyTradeBrain } from "../../../../lib/trade-brain";

export async function GET(request: Request) {
  const url = new URL(request.url);
  const limit = url.searchParams.get("limit") ?? "20";
  return proxyTradeBrain(
    `/v1/universe/scans?limit=${encodeURIComponent(limit)}`,
    "Chưa thể tải audit universe Binance.",
  );
}
