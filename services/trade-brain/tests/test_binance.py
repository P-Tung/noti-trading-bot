import asyncio
import unittest

import httpx

from trade_brain.binance import BinancePublicClient


class BinanceTests(unittest.TestCase):
    def test_parses_public_market_data(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/fapi/v1/klines":
                return httpx.Response(
                    200,
                    json=[[1000, "100", "105", "99", "104", "20", 2000, "2000", 12, "11", "1100", "0"]],
                )
            if request.url.path == "/fapi/v1/premiumIndex":
                return httpx.Response(
                    200,
                    json={
                        "symbol": "BTCUSDT",
                        "markPrice": "104",
                        "indexPrice": "103.9",
                        "lastFundingRate": "0.0001",
                        "nextFundingTime": 3000,
                    },
                )
            if request.url.path == "/fapi/v1/openInterest":
                return httpx.Response(200, json={"symbol": "BTCUSDT", "openInterest": "500", "time": 4000})
            if request.url.path == "/futures/data/openInterestHist":
                return httpx.Response(
                    200,
                    json=[
                        {"timestamp": 0, "sumOpenInterest": "500"},
                        {"timestamp": 3600000, "sumOpenInterest": "550"},
                    ],
                )
            if request.url.path == "/fapi/v1/ticker/24hr":
                return httpx.Response(
                    200,
                    json={"symbol": "BTCUSDT", "quoteVolume": "1000000", "volume": "10000", "count": 5000},
                )
            if request.url.path == "/fapi/v1/ticker/bookTicker":
                return httpx.Response(
                    200,
                    json={
                        "symbol": "BTCUSDT",
                        "bidPrice": "103.9",
                        "bidQty": "2",
                        "askPrice": "104.1",
                        "askQty": "3",
                        "time": 5000,
                    },
                )
            if request.url.path == "/fapi/v1/exchangeInfo":
                return httpx.Response(
                    200,
                    json={
                        "symbols": [
                            {
                                "symbol": "BTCUSDT",
                                "filters": [
                                    {"filterType": "LOT_SIZE", "minQty": "0.001", "stepSize": "0.001"},
                                    {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
                                ],
                            }
                        ]
                    },
                )
            return httpx.Response(404)

        async def run() -> None:
            client = BinancePublicClient(httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://test"))
            klines = await client.get_klines("BTCUSDT", "15m", 1)
            mark = await client.get_mark_price("BTCUSDT")
            open_interest = await client.get_open_interest("BTCUSDT")
            ticker_24h = await client.get_24h_ticker("BTCUSDT")
            oi_history = await client.get_open_interest_history("BTCUSDT")
            book_ticker = await client.get_book_ticker("BTCUSDT")
            rules = await client.get_symbol_rules("BTCUSDT")
            self.assertEqual(klines[0].close, 104.0)
            self.assertEqual(mark["mark_price"], 104.0)
            self.assertEqual(open_interest["open_interest"], 500.0)
            self.assertEqual(ticker_24h["quote_volume"], 1000000.0)
            self.assertEqual(oi_history[-1].open_interest, 550.0)
            self.assertEqual(book_ticker.bid_price, 103.9)
            self.assertEqual(book_ticker.ask_price, 104.1)
            self.assertEqual(rules.quantity_step, 0.001)
            self.assertEqual(rules.price_tick, 0.1)

        asyncio.run(run())

    def test_rejects_invalid_limit(self) -> None:
        client = BinancePublicClient(httpx.AsyncClient(base_url="https://test"))

        async def run() -> None:
            with self.assertRaises(ValueError):
                await client.get_klines("BTCUSDT", "15m", 1501)
            await client.close()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
