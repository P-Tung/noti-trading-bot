import { proxyTradeBrain } from "../../../../lib/trade-brain";

export async function GET() {
  return proxyTradeBrain("/v1/paper/accounts", "Paper accounts chưa thể kết nối Trade Brain.");
}
