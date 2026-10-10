import { proxyTradeBrain } from "../../../../lib/trade-brain";

export async function GET() {
  return proxyTradeBrain("/v1/config/saved", "Không thể tải cấu hình đã lưu.");
}

export async function POST(request: Request) {
  const body = await request.text();
  return proxyTradeBrain("/v1/config/saved", "Không thể lưu bản cấu hình.", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body,
  });
}
