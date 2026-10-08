import { proxyTradeBrain } from "../../../lib/trade-brain";

export async function GET() {
  return proxyTradeBrain("/health", "Trade Brain chưa thể kiểm tra trạng thái.");
}
