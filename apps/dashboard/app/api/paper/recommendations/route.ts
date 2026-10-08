import { proxyTradeBrain } from "../../../../lib/trade-brain";

export async function GET() {
  return proxyTradeBrain("/v1/paper/recommendations", "Paper journal chưa thể kết nối Trade Brain.");
}
