import { proxyTradeBrain } from "../../../lib/trade-brain";

export async function GET() {
  return proxyTradeBrain("/v1/config", "Không thể tải cấu hình Trade Brain.");
}

export async function PUT(request: Request) {
  const body = await request.text();
  return proxyTradeBrain("/v1/config", "Không thể lưu cấu hình Trade Brain.", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body,
  });
}
