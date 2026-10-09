import { NextResponse } from "next/server";

type BinanceExchangeSymbol = {
  symbol?: string;
  baseAsset?: string;
  quoteAsset?: string;
  marginAsset?: string;
  contractType?: string;
  status?: string;
};

const fallbackSymbols = [
  "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
  "LTCUSDT", "BCHUSDT", "UNIUSDT", "APTUSDT", "ARBUSDT", "OPUSDT", "SUIUSDT", "NEARUSDT", "FILUSDT", "INJUSDT",
  "ATOMUSDT", "TRXUSDT", "ETCUSDT", "XLMUSDT", "ALGOUSDT", "AAVEUSDT", "PEPEUSDT", "WIFUSDT", "SEIUSDT", "TIAUSDT",
].map((symbol) => ({ symbol, baseAsset: symbol.replace("USDT", ""), quoteAsset: "USDT", contractType: "PERPETUAL" }));

function normaliseSymbols(payload: { symbols?: BinanceExchangeSymbol[] }) {
  return (payload.symbols ?? [])
    .filter((item) => item.status === "TRADING" && item.contractType === "PERPETUAL" && item.quoteAsset === "USDT" && item.marginAsset === "USDT")
    .map((item) => ({ symbol: item.symbol, baseAsset: item.baseAsset, quoteAsset: item.quoteAsset, contractType: item.contractType }))
    .filter((item): item is { symbol: string; baseAsset: string; quoteAsset: string; contractType: string } => Boolean(item.symbol && item.baseAsset && item.quoteAsset && item.contractType))
    .sort((left, right) => left.symbol.localeCompare(right.symbol));
}

export async function GET() {
  try {
    const response = await fetch("https://www.binance.com/fapi/v1/exchangeInfo", {
      cache: "no-store",
      headers: { accept: "application/json" },
    });
    if (response.ok) {
      const payload = await response.json() as { symbols?: BinanceExchangeSymbol[] };
      const symbols = normaliseSymbols(payload);
      if (symbols.length > 0) return NextResponse.json({ symbols, source: "binance-futures" }, { headers: { "cache-control": "public, max-age=300" } });
    }
    return NextResponse.json({ symbols: fallbackSymbols, source: "binance-futures-fallback", warning: "Binance Futures tạm thời không phản hồi từ Cloudflare." }, { headers: { "cache-control": "public, max-age=300" } });
  } catch {
    return NextResponse.json({ symbols: fallbackSymbols, source: "binance-futures-fallback", warning: "Binance Futures tạm thời không phản hồi từ Cloudflare." }, { headers: { "cache-control": "public, max-age=300" } });
  }
}
