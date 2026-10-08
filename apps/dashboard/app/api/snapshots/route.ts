import { proxyTradeBrain } from "../../../lib/trade-brain";

export async function GET(request: Request) {
  const url = new URL(request.url);
  const limit = url.searchParams.get("limit") ?? "50";
  return proxyTradeBrain(
    `/v1/snapshots?limit=${encodeURIComponent(limit)}`,
    "Trade Brain chưa chạy hoặc chưa thể kết nối.",
  );
}
