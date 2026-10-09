import { NextResponse } from "next/server";

type BinanceExchangeSymbol = {
  symbol?: string;
  baseAsset?: string;
  quoteAsset?: string;
  marginAsset?: string;
  contractType?: string;
  status?: string;
};

export async function GET() {
  try {
    const response = await fetch("https://www.binance.com/fapi/v1/exchangeInfo", {
      cache: "no-store",
      headers: { accept: "application/json" },
    });
    if (!response.ok) {
      return NextResponse.json({ error: "Binance không trả về danh sách mã." }, { status: 502 });
    }
    const payload = await response.json() as { symbols?: BinanceExchangeSymbol[] };
    const symbols = (payload.symbols ?? [])
      .filter((item) => item.status === "TRADING" && item.contractType === "PERPETUAL" && item.quoteAsset === "USDT" && item.marginAsset === "USDT")
      .map((item) => ({ symbol: item.symbol, baseAsset: item.baseAsset, quoteAsset: item.quoteAsset, contractType: item.contractType }))
      .filter((item): item is { symbol: string; baseAsset: string; quoteAsset: string; contractType: string } => Boolean(item.symbol && item.baseAsset && item.quoteAsset && item.contractType))
      .sort((left, right) => left.symbol.localeCompare(right.symbol));
    return NextResponse.json({ symbols }, { headers: { "cache-control": "public, max-age=300" } });
  } catch {
    return NextResponse.json({ error: "Không thể kết nối Binance lúc này." }, { status: 503 });
  }
}
