import { proxyTradeBrain } from "../../../../lib/trade-brain";

export async function GET() {
  return proxyTradeBrain("/v1/evaluation/status", "Chưa thể đọc tiến độ queue đánh giá.");
}
