"""Small typed client for Binance USDⓈ-M public market-data endpoints."""

from dataclasses import dataclass
import os
import httpx


class BinanceClientError(RuntimeError):
    """Raised when Binance data cannot be fetched or parsed."""


@dataclass(frozen=True, slots=True)
class BinanceKline:
    opened_at_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    closed_at_ms: int
    quote_volume: float
    trade_count: int
    taker_buy_base_volume: float
    taker_buy_quote_volume: float


@dataclass(frozen=True, slots=True)
class BinanceDepth:
    last_update_id: int
    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]


@dataclass(frozen=True, slots=True)
class BinanceBookTicker:
    """Best bid and ask used by the paper fill model."""

    symbol: str
    bid_price: float
    bid_quantity: float
    ask_price: float
    ask_quantity: float
    observed_at_ms: int


@dataclass(frozen=True, slots=True)
class BinanceSymbolRules:
    """Exchange filters needed for safe paper sizing and price rounding."""

    symbol: str
    quantity_step: float
    minimum_quantity: float
    price_tick: float


@dataclass(frozen=True, slots=True)
class BinanceOpenInterestSample:
    """One timestamped aggregate open-interest observation."""

    timestamp_ms: int
    open_interest: float


class BinancePublicClient:
    """Async public client with strict request and response handling."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        base_url: str | None = None,
    ) -> None:
        resolved_base_url = base_url or os.environ.get("BINANCE_BASE_URL", "https://fapi.binance.com")
        self._client = client or httpx.AsyncClient(base_url=resolved_base_url, timeout=10.0)
        self._owns_client = client is None

    async def close(self) -> None:
        """Close the underlying client when this class created it."""
        if self._owns_client:
            await self._client.aclose()

    async def get_klines(self, symbol: str, interval: str, limit: int = 500) -> list[BinanceKline]:
        """Fetch completed and in-progress candles from Binance."""
        self._validate_request(symbol, interval, limit)
        payload = await self._get("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
        if not isinstance(payload, list):
            raise BinanceClientError("Binance klines response must be a list")
        return [self._parse_kline(item) for item in payload]

    async def get_mark_price(self, symbol: str) -> dict[str, float | int | str]:
        """Fetch current mark price and funding metadata."""
        self._validate_request(symbol, "1m", 1)
        payload = await self._get("/fapi/v1/premiumIndex", {"symbol": symbol})
        if not isinstance(payload, dict):
            raise BinanceClientError("Binance mark-price response must be an object")
        return {
            "symbol": str(payload["symbol"]),
            "mark_price": float(payload["markPrice"]),
            "index_price": float(payload["indexPrice"]),
            "last_funding_rate": float(payload["lastFundingRate"]),
            "next_funding_time_ms": int(payload["nextFundingTime"]),
        }

    async def get_open_interest(self, symbol: str) -> dict[str, float | int | str]:
        """Fetch current open interest from Binance."""
        self._validate_request(symbol, "1m", 1)
        payload = await self._get("/fapi/v1/openInterest", {"symbol": symbol})
        if not isinstance(payload, dict):
            raise BinanceClientError("Binance open-interest response must be an object")
        return {
            "symbol": str(payload["symbol"]),
            "open_interest": float(payload["openInterest"]),
            "time_ms": int(payload["time"]),
        }

    async def get_24h_ticker(self, symbol: str) -> dict[str, float | int | str]:
        """Fetch the rolling 24-hour quote-volume context for one symbol."""
        self._validate_request(symbol, "1m", 1)
        payload = await self._get("/fapi/v1/ticker/24hr", {"symbol": symbol})
        if not isinstance(payload, dict):
            raise BinanceClientError("Binance 24-hour ticker response must be an object")
        return {
            "symbol": str(payload["symbol"]),
            "quote_volume": float(payload["quoteVolume"]),
            "volume": float(payload["volume"]),
            "trade_count": int(payload["count"]),
        }

    async def get_open_interest_history(
        self,
        symbol: str,
        period: str = "5m",
        limit: int = 50,
    ) -> list[BinanceOpenInterestSample]:
        """Fetch timestamped public aggregate OI observations for research features."""
        self._validate_request(symbol, period, limit)
        payload = await self._get(
            "/futures/data/openInterestHist",
            {"symbol": symbol, "period": period, "limit": limit},
        )
        if not isinstance(payload, list):
            raise BinanceClientError("Binance OI-history response must be a list")
        return [
            BinanceOpenInterestSample(
                timestamp_ms=int(item["timestamp"]),
                open_interest=float(item["sumOpenInterest"]),
            )
            for item in payload
            if isinstance(item, dict)
        ]

    async def get_symbol_rules(self, symbol: str) -> BinanceSymbolRules:
        """Fetch symbol filters from the public futures exchange metadata."""
        self._validate_request(symbol, "1m", 1)
        payload = await self._get("/fapi/v1/exchangeInfo", {})
        if not isinstance(payload, dict) or not isinstance(payload.get("symbols"), list):
            raise BinanceClientError("Binance exchange-info response must contain symbols")
        row = next(
            (item for item in payload["symbols"] if isinstance(item, dict) and item.get("symbol") == symbol),
            None,
        )
        if not isinstance(row, dict) or not isinstance(row.get("filters"), list):
            raise BinanceClientError(f"Binance exchange-info has no filters for {symbol}")
        filters = {
            str(item.get("filterType")): item
            for item in row["filters"]
            if isinstance(item, dict) and item.get("filterType")
        }
        lot_size = filters.get("LOT_SIZE")
        price_filter = filters.get("PRICE_FILTER")
        if not isinstance(lot_size, dict) or not isinstance(price_filter, dict):
            raise BinanceClientError(f"Binance exchange-info filters are incomplete for {symbol}")
        return BinanceSymbolRules(
            symbol=symbol,
            quantity_step=float(lot_size["stepSize"]),
            minimum_quantity=float(lot_size["minQty"]),
            price_tick=float(price_filter["tickSize"]),
        )

    async def get_depth(self, symbol: str, limit: int = 100) -> BinanceDepth:
        """Fetch a local-order-book bootstrap snapshot."""
        self._validate_request(symbol, "1m", limit)
        payload = await self._get("/fapi/v1/depth", {"symbol": symbol, "limit": limit})
        if not isinstance(payload, dict):
            raise BinanceClientError("Binance depth response must be an object")
        return BinanceDepth(
            last_update_id=int(payload["lastUpdateId"]),
            bids=self._parse_levels(payload["bids"]),
            asks=self._parse_levels(payload["asks"]),
        )

    async def get_book_ticker(self, symbol: str) -> BinanceBookTicker:
        """Fetch the current best bid and ask from the public futures API."""
        self._validate_request(symbol, "1m", 1)
        payload = await self._get("/fapi/v1/ticker/bookTicker", {"symbol": symbol})
        if not isinstance(payload, dict):
            raise BinanceClientError("Binance book-ticker response must be an object")
        return BinanceBookTicker(
            symbol=str(payload["symbol"]),
            bid_price=float(payload["bidPrice"]),
            bid_quantity=float(payload["bidQty"]),
            ask_price=float(payload["askPrice"]),
            ask_quantity=float(payload["askQty"]),
            observed_at_ms=int(payload.get("time", 0)),
        )

    async def _get(self, path: str, params: dict[str, str | int]) -> object:
        try:
            response = await self._client.get(path, params=params)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            raise BinanceClientError(f"Binance request failed: {error}") from error

    @staticmethod
    def _validate_request(symbol: str, interval: str, limit: int) -> None:
        if not symbol or not symbol.isupper():
            raise ValueError("symbol must be a non-empty uppercase Binance symbol")
        if not interval:
            raise ValueError("interval is required")
        if not 1 <= limit <= 1500:
            raise ValueError("limit must be between 1 and 1500")

    @staticmethod
    def _parse_kline(item: object) -> BinanceKline:
        if not isinstance(item, list) or len(item) < 11:
            raise BinanceClientError("Binance kline row has an invalid shape")
        return BinanceKline(
            opened_at_ms=int(item[0]),
            open=float(item[1]),
            high=float(item[2]),
            low=float(item[3]),
            close=float(item[4]),
            volume=float(item[5]),
            closed_at_ms=int(item[6]),
            quote_volume=float(item[7]),
            trade_count=int(item[8]),
            taker_buy_base_volume=float(item[9]),
            taker_buy_quote_volume=float(item[10]),
        )

    @staticmethod
    def _parse_levels(levels: object) -> tuple[tuple[float, float], ...]:
        if not isinstance(levels, list):
            raise BinanceClientError("Binance depth levels must be a list")
        parsed: list[tuple[float, float]] = []
        for level in levels:
            if not isinstance(level, list) or len(level) < 2:
                raise BinanceClientError("Binance depth level has an invalid shape")
            parsed.append((float(level[0]), float(level[1])))
        return tuple(parsed)
