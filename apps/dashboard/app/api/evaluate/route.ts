import { proxyTradeBrain } from "../../../lib/trade-brain";

export async function POST() {
  return proxyTradeBrain(
    "/v1/evaluate",
    "Không thể chạy đánh giá lúc này.",
    { method: "POST" },
  );
}
