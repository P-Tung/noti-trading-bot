import { proxyTradeBrain } from "../../../lib/trade-brain";

export async function GET() {
  return proxyTradeBrain("/v1/reports?milestone=1", "Report chưa thể kết nối Trade Brain.");
}
