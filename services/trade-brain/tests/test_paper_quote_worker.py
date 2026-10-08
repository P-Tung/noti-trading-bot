import asyncio
import json

import httpx

from trade_brain.binance import BinanceBookTicker, BinanceClientError, BinancePublicClient
from trade_brain.paper_quote_worker import PaperQuoteWorker


def test_quote_worker_reads_book_ticker_and_posts_paper_quote() -> None:
    posted: list[dict[str, object]] = []

    async def brain_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "recommendations": [
                        {
                            "recommendation_id": "recommendation-1",
                            "symbol": "BTCUSDT",
                            "state": "OPEN",
                        }
                    ]
                },
            )
        posted.append(request.read())
        return httpx.Response(
            200,
            json={
                "action": "MARKED_OR_OPEN",
                "recommendation": {"state": "OPEN"},
            },
        )

    async def market_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/fapi/v1/premiumIndex":
            return httpx.Response(
                200,
                json={
                    "symbol": "BTCUSDT",
                    "markPrice": "104",
                    "indexPrice": "103.9",
                    "lastFundingRate": "0.0001",
                    "nextFundingTime": 28_805_000,
                },
            )
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

    async def run():
        brain_client = httpx.AsyncClient(
            transport=httpx.MockTransport(brain_handler),
            base_url="https://brain.test",
        )
        market_client = BinancePublicClient(
            httpx.AsyncClient(
                transport=httpx.MockTransport(market_handler),
                base_url="https://binance.test",
            )
        )
        worker = PaperQuoteWorker(brain_client, market_client, "https://brain.test")
        results = await worker.run_once()
        await brain_client.aclose()
        await market_client.close()
        return results

    results = asyncio.run(run())

    assert results[0].action == "MARKED_OR_OPEN"
    assert "funding_rate" in json.loads(posted[0])
    assert results[0].state == "OPEN"
    assert b'"bid":103.9' in posted[0]
    assert b'"ask":104.1' in posted[0]


def test_quote_worker_reports_missing_symbol_without_posting() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"recommendations": [{"recommendation_id": "missing-symbol", "state": "OPEN"}]})

    async def run():
        brain_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://brain.test")
        market_client = BinancePublicClient(httpx.AsyncClient(base_url="https://binance.test"))
        worker = PaperQuoteWorker(brain_client, market_client, "https://brain.test")
        results = await worker.run_once()
        await brain_client.aclose()
        await market_client.close()
        return results

    results = asyncio.run(run())
    assert results[0].error == "recommendation is missing id or symbol"


def test_quote_worker_continues_after_one_binance_failure() -> None:
    class PartialMarketClient:
        async def get_book_ticker(self, symbol: str) -> BinanceBookTicker:
            if symbol == "BADUSDT":
                raise BinanceClientError("temporary market-data failure")
            return BinanceBookTicker(symbol, 100, 1, 101, 1, 5000)

        async def get_mark_price(self, symbol: str) -> dict[str, float | int | str]:
            return {
                "symbol": symbol,
                "mark_price": 100.5,
                "index_price": 100.5,
                "last_funding_rate": 0,
                "next_funding_time_ms": 28_805_000,
            }

    posted = 0

    async def brain_handler(request: httpx.Request) -> httpx.Response:
        nonlocal posted
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "recommendations": [
                        {"recommendation_id": "bad", "symbol": "BADUSDT", "state": "OPEN"},
                        {"recommendation_id": "good", "symbol": "GOODUSDT", "state": "OPEN"},
                    ]
                },
            )
        posted += 1
        return httpx.Response(200, json={"action": "MARKED_OR_OPEN", "recommendation": {"state": "OPEN"}})

    async def run():
        brain_client = httpx.AsyncClient(
            transport=httpx.MockTransport(brain_handler),
            base_url="https://brain.test",
        )
        worker = PaperQuoteWorker(brain_client, PartialMarketClient(), "https://brain.test")
        results = await worker.run_once()
        await brain_client.aclose()
        return results

    results = asyncio.run(run())

    assert results[0].error == "temporary market-data failure"
    assert results[1].action == "MARKED_OR_OPEN"
    assert posted == 1


def test_quote_worker_marks_quote_degraded_when_funding_context_fails() -> None:
    posted: list[dict[str, object]] = []

    class MarketWithoutFunding:
        async def get_book_ticker(self, symbol: str) -> BinanceBookTicker:
            return BinanceBookTicker(symbol, 100, 1, 101, 1, 5000)

        async def get_mark_price(self, symbol: str) -> dict[str, float | int | str]:
            raise BinanceClientError("funding endpoint unavailable")

    async def brain_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"recommendations": [{"recommendation_id": "degraded", "symbol": "BTCUSDT", "state": "OPEN"}]},
            )
        posted.append(json.loads(request.read()))
        return httpx.Response(200, json={"action": "MARKED_OR_OPEN", "recommendation": {"state": "OPEN"}})

    async def run():
        brain_client = httpx.AsyncClient(transport=httpx.MockTransport(brain_handler), base_url="https://brain.test")
        worker = PaperQuoteWorker(brain_client, MarketWithoutFunding(), "https://brain.test")
        results = await worker.run_once()
        await brain_client.aclose()
        return results

    results = asyncio.run(run())

    assert results[0].error is None
    assert posted[0]["quality_status"] == "DEGRADED"
