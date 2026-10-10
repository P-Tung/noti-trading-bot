import { proxyTradeBrain } from "../../../../../lib/trade-brain";

export async function GET(
  _request: Request,
  context: { params: Promise<{ configId: string }> },
) {
  const { configId } = await context.params;
  return proxyTradeBrain(
    `/v1/config/saved/${encodeURIComponent(configId)}`,
    "Không thể tải bản cấu hình đã lưu.",
  );
}
