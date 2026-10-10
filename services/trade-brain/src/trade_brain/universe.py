"""V2 Binance universe selection with auditable, fail-closed filtering."""

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from trade_brain.binance import BinancePublicClient
from trade_brain.configuration import UniverseConfig


@dataclass(frozen=True, slots=True)
class UniverseSelection:
    """The immutable result of one universe refresh."""

    scan_id: str
    observed_at: datetime
    symbols: tuple[str, ...]
    input_ticker_count: int
    registry_count: int
    passed_count: int
    exclusion_counts: dict[str, int]


async def select_binance_universe(
    client: BinancePublicClient,
    policy: UniverseConfig,
    now: datetime | None = None,
) -> UniverseSelection:
    """Keep every eligible USDT perpetual above the V2 volume threshold."""
    observed_at = now or datetime.now(timezone.utc)
    exchange_info = await client.get_exchange_info()
    tickers = await client.get_24h_tickers()
    registry = {
        str(row.get("symbol")): row
        for row in exchange_info
        if isinstance(row, dict) and row.get("symbol")
    }
    volume_by_symbol = {
        str(row.get("symbol")): row
        for row in tickers
        if isinstance(row, dict) and row.get("symbol") and row.get("quote_volume") is not None
    }
    exclusions: dict[str, int] = {}
    passed_with_volume: list[tuple[str, float]] = []
    now_ms = int(observed_at.timestamp() * 1000)
    for symbol, ticker in volume_by_symbol.items():
        row = registry.get(symbol)
        reason = _exclusion_reason(row, ticker, policy, now_ms)
        if reason is not None:
            exclusions[reason] = exclusions.get(reason, 0) + 1
            continue
        passed_with_volume.append((symbol, float(ticker["quote_volume"])))
    passed_with_volume.sort(key=lambda item: (-item[1], item[0]))
    if policy.max_symbols is not None:
        passed_with_volume = passed_with_volume[: policy.max_symbols]
    passed = sorted(symbol for symbol, _ in passed_with_volume)
    return UniverseSelection(
        scan_id=f"universe-{uuid4().hex}",
        observed_at=observed_at,
        symbols=tuple(passed),
        input_ticker_count=len(volume_by_symbol),
        registry_count=len(registry),
        passed_count=len(passed),
        exclusion_counts=exclusions,
    )


def _exclusion_reason(
    row: dict[str, object] | None,
    ticker: dict[str, object],
    policy: UniverseConfig,
    now_ms: int,
) -> str | None:
    if row is None:
        return "ASSET_CLASS_UNKNOWN"
    if row.get("status") != policy.status:
        return "SYMBOL_NOT_TRADING"
    if row.get("contractType") != policy.contract_type:
        return "OUT_OF_SCOPE"
    if row.get("quoteAsset") != policy.quote_asset:
        return "OUT_OF_SCOPE"
    if row.get("marginAsset") != policy.margin_asset:
        return "OUT_OF_SCOPE"
    window_end_ms = int(ticker.get("window_end_ms", 0) or 0)
    if window_end_ms and now_ms - window_end_ms > policy.max_universe_age_seconds * 1000:
        return "UNIVERSE_STALE"
    quote_volume = float(ticker["quote_volume"])
    if quote_volume < policy.min_quote_volume_24h_usdt:
        return "VOLUME_BELOW_MIN"
    return None


def manual_universe(symbols: list[str]) -> tuple[str, ...]:
    """Normalize the explicit manual fallback without applying a hidden cap."""
    return tuple(dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip()))
