"""Public Binance data collection into immutable Trade Brain snapshots."""

from datetime import datetime, timedelta, timezone

import httpx

from trade_brain.binance import BinanceClientError, BinanceKline, BinancePublicClient
from dataclasses import dataclass

from trade_brain.contracts import FeatureValue, MarketSnapshot, QualityStatus
from trade_brain.market_data import Bar, PivotKind, adx_wilder, atr_wilder, confirmed_pivots, ema, relative_volume
from trade_brain.snapshots import create_snapshot
from trade_brain.storage import SnapshotStore

TIMEFRAMES = ("1d", "4h", "1h", "15m")
MINIMUM_1D_CANDLES = 250
MINIMUM_1D_FETCH_DAYS = 300


@dataclass(frozen=True, slots=True)
class CollectedMarketData:
    """One decision-time snapshot and the exact closed bars used by strategies."""

    snapshot: MarketSnapshot
    bars_by_timeframe: dict[str, list[Bar]]


class PublicSnapshotCollector:
    """Collect a minimal price-only snapshot for one symbol."""

    def __init__(self, client: BinancePublicClient, store: SnapshotStore) -> None:
        self._client = client
        self._store = store

    async def collect(self, experiment_id: str, symbol: str, now: datetime | None = None) -> MarketSnapshot:
        """Fetch closed 15m candles, calculate starter features, and persist."""
        snapshot, _ = await self.collect_with_bars(experiment_id, symbol, now)
        return snapshot

    async def collect_with_bars(
        self,
        experiment_id: str,
        symbol: str,
        now: datetime | None = None,
    ) -> tuple[MarketSnapshot, list[Bar]]:
        """Persist a snapshot and return the exact closed bars used to calculate it."""
        collected = await self.collect_timeframes(experiment_id, symbol, ("15m",), now)
        return collected.snapshot, collected.bars_by_timeframe["15m"]

    async def collect_timeframes(
        self,
        experiment_id: str,
        symbol: str,
        timeframes: tuple[str, ...] = TIMEFRAMES,
        now: datetime | None = None,
        history_days: int = 180,
    ) -> CollectedMarketData:
        """Fetch closed candles for all requested timeframes before persisting one snapshot."""
        if not timeframes or any(timeframe not in TIMEFRAMES for timeframe in timeframes):
            raise ValueError("timeframes must be selected from the supported Trade V2 timeframes")
        decision_time = now or datetime.now(timezone.utc)
        if history_days < 1:
            raise ValueError("history_days must be positive")
        bars_by_timeframe: dict[str, list[Bar]] = {}
        for timeframe in timeframes:
            fetch_history_days = _fetch_history_days(timeframe, history_days)
            raw_klines = await _load_klines(
                self._client,
                symbol,
                timeframe,
                decision_time,
                _history_bars(timeframe, fetch_history_days),
                fetch_history_days,
            )
            closed_klines = _closed_klines(raw_klines, decision_time)
            minimum_bars = (
                MINIMUM_1D_CANDLES
                if timeframe == "1d" and callable(getattr(self._client, "get_klines_history", None))
                else 20
            )
            if len(closed_klines) < minimum_bars:
                raise ValueError(f"at least {minimum_bars} closed {timeframe} candles are required")
            bars_by_timeframe[timeframe] = [_to_bar(kline) for kline in closed_klines]
        features = _features_for_timeframes(bars_by_timeframe, decision_time)
        features.update(await _current_market_features(symbol, decision_time, self._client))
        snapshot = create_snapshot(
            experiment_id,
            symbol,
            decision_time,
            features,
            quality_status=_snapshot_quality(features),
        )
        self._store.save_snapshot(snapshot)
        return CollectedMarketData(snapshot, bars_by_timeframe)


async def _load_klines(
    client: BinancePublicClient,
    symbol: str,
    timeframe: str,
    decision_time: datetime,
    max_bars: int,
    history_days: int,
) -> list[BinanceKline]:
    history_loader = getattr(client, "get_klines_history", None)
    if callable(history_loader):
        start_time_ms = int((decision_time - timedelta(days=history_days)).timestamp() * 1000)
        return await history_loader(symbol, timeframe, start_time_ms, max_bars=max_bars)
    return await client.get_klines(symbol, timeframe, min(250, max_bars))


def _history_bars(timeframe: str, history_days: int) -> int:
    bars_per_day = {"1d": 1, "4h": 6, "1h": 24, "15m": 96}
    return max(300 if timeframe == "1d" else 1_500, history_days * bars_per_day[timeframe])


def _fetch_history_days(timeframe: str, minimum_history_days: int) -> int:
    """Fetch extra daily history when a strategy indicator needs warmup."""
    if timeframe == "1d":
        return max(minimum_history_days, MINIMUM_1D_FETCH_DAYS)
    return minimum_history_days


def _closed_klines(klines: list[BinanceKline], now: datetime) -> list[BinanceKline]:
    now_ms = int(now.timestamp() * 1000)
    return [kline for kline in klines if kline.closed_at_ms <= now_ms]


def _to_bar(kline: BinanceKline) -> Bar:
    return Bar(
        opened_at=datetime.fromtimestamp(kline.opened_at_ms / 1000, timezone.utc),
        closed_at=datetime.fromtimestamp(kline.closed_at_ms / 1000, timezone.utc),
        open=kline.open,
        high=kline.high,
        low=kline.low,
        close=kline.close,
        volume=kline.volume,
        quote_volume=kline.quote_volume,
        trade_count=kline.trade_count,
        taker_buy_base_volume=kline.taker_buy_base_volume,
        taker_buy_quote_volume=kline.taker_buy_quote_volume,
    )


def _feature(value: float, unit: str, observed_at: datetime) -> FeatureValue:
    return FeatureValue(
        value=value,
        unit=unit,
        observed_at=observed_at,
        available_at=observed_at,
        quality_status=QualityStatus.VALID,
    )


async def _current_market_features(
    symbol: str,
    observed_at: datetime,
    client: BinancePublicClient,
) -> dict[str, FeatureValue]:
    """Collect current derivatives and liquidity context without inventing gaps."""
    features: dict[str, FeatureValue] = {}
    fetchers = {
        name: getattr(client, name)
        for name in (
            "get_mark_price",
            "get_open_interest",
            "get_open_interest_history",
            "get_24h_ticker",
            "get_book_ticker",
            "get_depth",
            "get_symbol_rules",
        )
        if callable(getattr(client, name, None))
    }
    fetchers = {
        name.removeprefix("get_"): fetcher
        for name, fetcher in fetchers.items()
    }
    results: dict[str, object] = {}
    for name, fetcher in fetchers.items():
        try:
            results[name] = await fetcher(symbol)
        except (BinanceClientError, httpx.HTTPError, KeyError, TypeError, ValueError):
            features[f"{name}_quality"] = FeatureValue(
                value=None,
                unit="status",
                observed_at=observed_at,
                available_at=observed_at,
                quality_status=QualityStatus.DEGRADED,
            )

    mark = results.get("mark_price")
    if isinstance(mark, dict):
        features["mark_price"] = _feature(float(mark["mark_price"]), "USDT", observed_at)
        features["index_price"] = _feature(float(mark["index_price"]), "USDT", observed_at)
        features["funding_rate"] = _feature(float(mark["last_funding_rate"]), "ratio", observed_at)
        minutes_to_funding = max(
            0.0,
            (int(mark["next_funding_time_ms"]) / 1000 - observed_at.timestamp()) / 60,
        )
        features["minutes_to_funding"] = _feature(minutes_to_funding, "minutes", observed_at)

    open_interest = results.get("open_interest")
    if isinstance(open_interest, dict):
        features["oi_quantity"] = _feature(float(open_interest["open_interest"]), "contracts", observed_at)

    if isinstance(mark, dict) and float(mark["index_price"]) > 0:
        features["mark_index_basis_bps"] = _feature(
            10000 * (float(mark["mark_price"]) - float(mark["index_price"])) / float(mark["index_price"]),
            "basis_points",
            observed_at,
        )

    oi_history = results.get("open_interest_history")
    if isinstance(oi_history, list):
        _add_oi_change_features(features, oi_history, observed_at)

    ticker_24h = results.get("24h_ticker")
    if isinstance(ticker_24h, dict):
        features["quote_volume_24h"] = _feature(
            float(ticker_24h["quote_volume"]),
            "USDT",
            observed_at,
        )

    ticker = results.get("book_ticker")
    if isinstance(ticker, object) and hasattr(ticker, "bid_price") and hasattr(ticker, "ask_price"):
        bid = float(ticker.bid_price)
        ask = float(ticker.ask_price)
        mid = (bid + ask) / 2
        if mid > 0:
            features["spread_bps"] = _feature(10000 * (ask - bid) / mid, "basis_points", observed_at)
            features["best_bid"] = _feature(bid, "USDT", observed_at)
            features["best_ask"] = _feature(ask, "USDT", observed_at)

    depth = results.get("depth")
    if depth is not None and hasattr(depth, "bids") and hasattr(depth, "asks"):
        bid_levels = tuple(depth.bids)
        ask_levels = tuple(depth.asks)
        if bid_levels and ask_levels:
            best_bid = float(bid_levels[0][0])
            best_ask = float(ask_levels[0][0])
            mid = (best_bid + best_ask) / 2
            lower = mid * (1 - 0.001)
            upper = mid * (1 + 0.001)
            bid_depth = sum(price * quantity for price, quantity in bid_levels if price >= lower)
            ask_depth = sum(price * quantity for price, quantity in ask_levels if price <= upper)
            features["depth_bid_usdt"] = _feature(bid_depth, "USDT", observed_at)
            features["depth_ask_usdt"] = _feature(ask_depth, "USDT", observed_at)
            total_depth = bid_depth + ask_depth
            if total_depth > 0:
                features["book_imbalance"] = _feature(
                    (bid_depth - ask_depth) / total_depth,
                    "ratio",
                    observed_at,
                )
    rules = results.get("symbol_rules")
    if rules is not None and all(
        hasattr(rules, field) for field in ("quantity_step", "minimum_quantity", "price_tick")
    ):
        features["quantity_step"] = _feature(float(rules.quantity_step), "base_asset", observed_at)
        features["minimum_quantity"] = _feature(float(rules.minimum_quantity), "base_asset", observed_at)
        features["price_tick"] = _feature(float(rules.price_tick), "USDT", observed_at)
    return features


def _add_oi_change_features(
    features: dict[str, FeatureValue],
    samples: list[object],
    observed_at: datetime,
) -> None:
    """Add OI changes only when an older timestamped sample exists."""
    valid_samples = sorted(
        (
            sample
            for sample in samples
            if hasattr(sample, "timestamp_ms") and hasattr(sample, "open_interest")
        ),
        key=lambda sample: sample.timestamp_ms,
    )
    if not valid_samples:
        return
    latest = valid_samples[-1]
    for label, lookback_ms in (("1h", 3_600_000), ("4h", 14_400_000)):
        target = latest.timestamp_ms - lookback_ms
        previous = next(
            (sample for sample in reversed(valid_samples[:-1]) if sample.timestamp_ms <= target),
            None,
        )
        if previous is None or previous.open_interest == 0:
            continue
        features[f"oi_change_{label}_pct"] = _feature(
            100 * (latest.open_interest / previous.open_interest - 1),
            "percent",
            observed_at,
        )


def _snapshot_quality(features: dict[str, FeatureValue]) -> QualityStatus:
    """Promote endpoint-level degradation to the immutable snapshot status."""
    statuses = {feature.quality_status for feature in features.values()}
    if QualityStatus.INVALID in statuses:
        return QualityStatus.INVALID
    if QualityStatus.AMBIGUOUS in statuses:
        return QualityStatus.AMBIGUOUS
    if QualityStatus.DEGRADED in statuses:
        return QualityStatus.DEGRADED
    return QualityStatus.VALID


def _features_for_timeframes(
    bars_by_timeframe: dict[str, list[Bar]],
    observed_at: datetime,
) -> dict[str, FeatureValue]:
    features: dict[str, FeatureValue] = {}
    for timeframe, bars in bars_by_timeframe.items():
        atr_values = atr_wilder(bars)
        latest_atr = atr_values[-1]
        latest_bar = bars[-1]
        if latest_atr is None or latest_atr <= 0:
            raise ValueError(f"latest ATR is unavailable for {timeframe}")
        volume_ratio = relative_volume(bars, len(bars) - 1)
        if volume_ratio is None:
            raise ValueError(f"latest relative volume is unavailable for {timeframe}")
        features[f"close_{timeframe}"] = _feature(latest_bar.close, "USDT", observed_at)
        features[f"data_age_ms_{timeframe}"] = _feature(
            max(0.0, (observed_at - latest_bar.closed_at).total_seconds() * 1000),
            "milliseconds",
            observed_at,
        )
        features[f"bar_gap_count_{timeframe}"] = _feature(
            _bar_gap_count(bars, timeframe),
            "bars",
            observed_at,
        )
        features[f"atr_{timeframe}"] = _feature(latest_atr, "USDT", observed_at)
        features[f"atr_pct_{timeframe}"] = _feature(100 * latest_atr / latest_bar.close, "percent", observed_at)
        features[f"relative_volume_{timeframe}"] = _feature(volume_ratio, "ratio", observed_at)
        if (
            latest_bar.quote_volume is not None
            and latest_bar.quote_volume > 0
            and latest_bar.taker_buy_quote_volume is not None
        ):
            features[f"delta_quote_{timeframe}"] = _feature(
                (2 * latest_bar.taker_buy_quote_volume) - latest_bar.quote_volume,
                "USDT_taker_proxy",
                observed_at,
            )
            features[f"taker_buy_fraction_{timeframe}"] = _feature(
                latest_bar.taker_buy_quote_volume / latest_bar.quote_volume,
                "ratio",
                observed_at,
            )
            sell_quote_volume = latest_bar.quote_volume - latest_bar.taker_buy_quote_volume
            if sell_quote_volume > 0:
                features[f"taker_buy_sell_ratio_{timeframe}"] = _feature(
                    latest_bar.taker_buy_quote_volume / sell_quote_volume,
                    "ratio",
                    observed_at,
                )
        if latest_bar.trade_count is not None:
            prior_trade_counts = [
                bar.trade_count
                for bar in bars[max(0, len(bars) - 21) : -1]
                if bar.trade_count is not None
            ]
            if prior_trade_counts:
                average_trade_count = sum(prior_trade_counts) / len(prior_trade_counts)
                if average_trade_count > 0:
                    features[f"trade_count_relative_{timeframe}"] = _feature(
                        latest_bar.trade_count / average_trade_count,
                        "ratio",
                        observed_at,
                    )
        if latest_bar.high > latest_bar.low:
            features[f"body_to_atr_{timeframe}"] = _feature(
                abs(latest_bar.close - latest_bar.open) / latest_atr,
                "ratio",
                observed_at,
            )
        features[f"close_location_{timeframe}"] = _feature(
                (latest_bar.close - latest_bar.low) / (latest_bar.high - latest_bar.low),
                "ratio",
                observed_at,
            )
        range_window = bars[max(0, len(bars) - 20) :]
        range_high = max(bar.high for bar in range_window)
        range_low = min(bar.low for bar in range_window)
        if range_high > range_low:
            features[f"range_position_{timeframe}"] = _feature(
                (latest_bar.close - range_low) / (range_high - range_low),
                "ratio",
                observed_at,
            )
        features[f"structure_state_{timeframe}"] = FeatureValue(
            value=_structure_state(bars),
            unit="state",
            observed_at=observed_at,
            available_at=observed_at,
            quality_status=QualityStatus.VALID,
        )
        ema20_values = ema(bars, 20)
        ema50_values = ema(bars, 50)
        ema200_values = ema(bars, 200)
        adx_values = adx_wilder(bars)
        ema20 = ema20_values[-1]
        ema50 = ema50_values[-1]
        ema200 = ema200_values[-1]
        adx = adx_values[-1]
        if ema20 is not None:
            features[f"ema20_{timeframe}"] = _feature(ema20, "USDT", observed_at)
            features[f"ema20_distance_atr_{timeframe}"] = _feature(
                (latest_bar.close - ema20) / latest_atr,
                "ATR",
                observed_at,
            )
            previous_ema20 = ema20_values[-6] if len(ema20_values) >= 6 else None
            if previous_ema20 is not None:
                features[f"ema20_slope_atr_{timeframe}"] = _feature(
                    (ema20 - previous_ema20) / latest_atr,
                    "ATR",
                    observed_at,
                )
        if ema50 is not None:
            features[f"ema50_{timeframe}"] = _feature(ema50, "USDT", observed_at)
            features[f"ema50_distance_atr_{timeframe}"] = _feature(
                (latest_bar.close - ema50) / latest_atr,
                "ATR",
                observed_at,
            )
        if ema20 is not None and ema50 is not None:
            features[f"ema20_50_gap_atr_{timeframe}"] = _feature(
                (ema20 - ema50) / latest_atr,
                "ATR",
                observed_at,
            )
        if ema200 is not None:
            features[f"ema200_{timeframe}"] = _feature(ema200, "USDT", observed_at)
            features[f"ema200_distance_atr_{timeframe}"] = _feature(
                (latest_bar.close - ema200) / latest_atr,
                "ATR",
                observed_at,
            )
        if adx is not None:
            features[f"adx14_{timeframe}"] = _feature(adx, "index", observed_at)
    return features


_TIMEFRAME_SECONDS = {"1d": 86400, "4h": 14400, "1h": 3600, "15m": 900, "5m": 300}


def _bar_gap_count(bars: list[Bar], timeframe: str) -> int:
    """Count missing expected candle slots without treating the current bar as a gap."""
    interval = _TIMEFRAME_SECONDS[timeframe]
    gaps = 0
    for previous, current in zip(bars, bars[1:]):
        elapsed = (current.opened_at - previous.opened_at).total_seconds()
        if elapsed > interval:
            gaps += max(0, round(elapsed / interval) - 1)
    return gaps


def _structure_state(bars: list[Bar]) -> str:
    """Classify only from pivots confirmed by completed bars."""
    pivots = confirmed_pivots(bars)
    highs = [pivot.price for pivot in pivots if pivot.kind is PivotKind.HIGH]
    lows = [pivot.price for pivot in pivots if pivot.kind is PivotKind.LOW]
    if len(highs) < 2 or len(lows) < 2:
        return "INSUFFICIENT"
    higher_highs = highs[-1] > highs[-2]
    higher_lows = lows[-1] > lows[-2]
    lower_highs = highs[-1] < highs[-2]
    lower_lows = lows[-1] < lows[-2]
    if higher_highs and higher_lows:
        return "BULLISH"
    if lower_highs and lower_lows:
        return "BEARISH"
    return "MIXED"
