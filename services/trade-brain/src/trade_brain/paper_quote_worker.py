"""Public-quote worker for the Trade V1 paper lifecycle."""

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from trade_brain.binance import BinanceClientError, BinancePublicClient
from trade_brain.contracts import QualityStatus

FUNDING_INTERVAL_SECONDS = 8 * 60 * 60


class PaperQuoteWorkerError(RuntimeError):
    """Raised when the Trade Brain paper quote contract is invalid."""


@dataclass(frozen=True, slots=True)
class QuoteUpdateResult:
    recommendation_id: str
    state: str | None
    action: str | None
    error: str | None = None


class PaperQuoteWorker:
    """Read public bid/ask data and advance paper recommendations only."""

    def __init__(
        self,
        brain_client: httpx.AsyncClient,
        market_client: BinancePublicClient,
        brain_url: str,
    ) -> None:
        self._brain_client = brain_client
        self._market_client = market_client
        self._brain_url = brain_url.rstrip("/")

    async def run_once(self) -> list[QuoteUpdateResult]:
        """Process every active recommendation returned by Trade Brain."""
        recommendations = await self._load_recommendations()
        results: list[QuoteUpdateResult] = []
        for recommendation in recommendations:
            if recommendation.get("state") not in {"RECOMMENDED", "OPEN"}:
                continue
            results.append(await self._process_recommendation(recommendation))
        return results

    async def _load_recommendations(self) -> list[dict[str, object]]:
        response = await self._brain_client.get(f"{self._brain_url}/v1/paper/recommendations")
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or not isinstance(body.get("recommendations"), list):
            raise PaperQuoteWorkerError("paper recommendations response has an invalid shape")
        return [item for item in body["recommendations"] if isinstance(item, dict)]

    async def _process_recommendation(self, recommendation: dict[str, object]) -> QuoteUpdateResult:
        recommendation_id = str(recommendation.get("recommendation_id", ""))
        symbol = recommendation.get("symbol")
        if not recommendation_id or not isinstance(symbol, str) or not symbol:
            return QuoteUpdateResult(recommendation_id, None, None, "recommendation is missing id or symbol")
        try:
            ticker = await self._market_client.get_book_ticker(symbol)
            quality_status = QualityStatus.VALID
            observed_at = (
                datetime.fromtimestamp(ticker.observed_at_ms / 1000, tz=timezone.utc)
                if ticker.observed_at_ms > 0
                else datetime.now(timezone.utc)
            )
            if ticker.observed_at_ms <= 0:
                quality_status = QualityStatus.DEGRADED
            quote_payload: dict[str, object] = {
                "observed_at": observed_at.isoformat(),
                "bid": ticker.bid_price,
                "ask": ticker.ask_price,
                "mark": (ticker.bid_price + ticker.ask_price) / 2,
                "quality_status": quality_status.value,
            }
            get_mark_price = getattr(self._market_client, "get_mark_price", None)
            if callable(get_mark_price):
                try:
                    mark_data = await get_mark_price(symbol)
                    next_funding_time_ms = int(mark_data["next_funding_time_ms"])
                    last_funding_time_ms = next_funding_time_ms - FUNDING_INTERVAL_SECONDS * 1000
                    if last_funding_time_ms <= ticker.observed_at_ms:
                        quote_payload["funding_rate"] = mark_data["last_funding_rate"]
                        quote_payload["funding_time"] = datetime.fromtimestamp(
                            last_funding_time_ms / 1000,
                            tz=timezone.utc,
                        ).isoformat()
                except (BinanceClientError, httpx.HTTPError, KeyError, TypeError, ValueError):
                    quote_payload["quality_status"] = QualityStatus.DEGRADED.value
            response = await self._brain_client.post(
                f"{self._brain_url}/v1/paper/recommendations/{recommendation_id}/quote",
                json=quote_payload,
            )
            response.raise_for_status()
            body = response.json()
            updated = body.get("recommendation") if isinstance(body, dict) else None
            if not isinstance(updated, dict):
                raise PaperQuoteWorkerError("paper quote response has no recommendation")
            return QuoteUpdateResult(
                recommendation_id,
                str(updated.get("state")) if updated.get("state") else None,
                str(body.get("action")) if isinstance(body, dict) and body.get("action") else None,
            )
        except (BinanceClientError, httpx.HTTPError, PaperQuoteWorkerError, ValueError, KeyError) as error:
            return QuoteUpdateResult(recommendation_id, None, None, str(error))


async def run_forever() -> None:
    """Poll public quotes at a safe interval and update paper state."""
    brain_url = os.environ.get("TRADE_BRAIN_URL", "http://127.0.0.1:8090")
    interval = int(os.environ.get("TRADE_QUOTE_INTERVAL_SECONDS", "15"))
    if interval < 5:
        raise RuntimeError("TRADE_QUOTE_INTERVAL_SECONDS must be at least 5")
    async with httpx.AsyncClient(timeout=10.0) as brain_client:
        market_client = BinancePublicClient()
        try:
            worker = PaperQuoteWorker(brain_client, market_client, brain_url)
            while True:
                await worker.run_once()
                await asyncio.sleep(interval)
        finally:
            await market_client.close()


def main() -> None:
    """Console entry point for public-data paper monitoring."""
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
