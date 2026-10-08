"""Periodic public-market snapshot collector for the Trade Brain."""

import asyncio
import logging
import os
from collections.abc import Sequence

from trade_brain.binance import BinanceClientError, BinancePublicClient
from trade_brain.collector import PublicSnapshotCollector
from trade_brain.storage import InMemorySnapshotStore, SnapshotStore
from trade_brain.supabase_store import SupabaseSnapshotStore

DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT")
DEFAULT_INTERVAL_SECONDS = 900
MIN_INTERVAL_SECONDS = 60
LOGGER = logging.getLogger(__name__)


def build_store() -> SnapshotStore:
    """Use Supabase in configured environments and memory locally."""
    if os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SECRET_KEY"):
        return SupabaseSnapshotStore.from_environment()
    return InMemorySnapshotStore()


def configured_symbols() -> tuple[str, ...]:
    """Read a comma-separated symbol list with a safe default."""
    raw_symbols = os.environ.get("TRADE_SYMBOLS", ",".join(DEFAULT_SYMBOLS))
    symbols = tuple(symbol.strip().upper() for symbol in raw_symbols.split(",") if symbol.strip())
    if not symbols:
        raise RuntimeError("TRADE_SYMBOLS must contain at least one symbol")
    return symbols


def configured_interval_seconds() -> int:
    """Read and validate the collection interval."""
    raw_interval = os.environ.get("TRADE_COLLECTION_INTERVAL_SECONDS", str(DEFAULT_INTERVAL_SECONDS))
    try:
        interval = int(raw_interval)
    except ValueError as error:
        raise RuntimeError("TRADE_COLLECTION_INTERVAL_SECONDS must be an integer") from error
    if interval < MIN_INTERVAL_SECONDS:
        raise RuntimeError(f"TRADE_COLLECTION_INTERVAL_SECONDS must be at least {MIN_INTERVAL_SECONDS}")
    return interval


async def collect_once(
    store: SnapshotStore,
    experiment_id: str,
    symbols: Sequence[str],
) -> int:
    """Collect one immutable PRICE_ONLY snapshot per configured symbol."""
    ensure_experiment = getattr(store, "ensure_experiment", None)
    if callable(ensure_experiment):
        ensure_experiment(experiment_id)

    client = BinancePublicClient()
    collector = PublicSnapshotCollector(client, store)
    try:
        collected_count = 0
        for symbol in symbols:
            try:
                await collector.collect(experiment_id=experiment_id, symbol=symbol)
                collected_count += 1
            except (BinanceClientError, ValueError) as error:
                LOGGER.warning("Skipping snapshot for %s: %s", symbol, error)
    finally:
        await client.close()
    return collected_count


async def run_forever() -> None:
    """Run the safe public-data collector until the process is stopped."""
    experiment_id = os.environ.get("TRADE_V1_EXPERIMENT_ID", "trade-v1-local")
    symbols = configured_symbols()
    interval_seconds = configured_interval_seconds()
    store = build_store()

    while True:
        await collect_once(store, experiment_id, symbols)
        await asyncio.sleep(interval_seconds)


def main() -> None:
    """Console entry point for the periodic collector."""
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
