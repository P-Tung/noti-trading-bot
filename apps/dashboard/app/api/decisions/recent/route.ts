import { proxyTradeBrain } from "../../../../lib/trade-brain";

export async function GET(request: Request) {
  const url = new URL(request.url);
  const limit = url.searchParams.get("limit") ?? "20";
  return proxyTradeBrain(
    `/v1/decisions/recent?limit=${encodeURIComponent(limit)}`,
    "Decision history chưa thể kết nối Trade Brain.",
  );
}
