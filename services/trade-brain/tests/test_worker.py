import pytest
import asyncio

from trade_brain import worker
from trade_brain.storage import InMemorySnapshotStore


def test_configured_symbols_uses_safe_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRADE_SYMBOLS", raising=False)

    assert worker.configured_symbols() == ("BTCUSDT", "ETHUSDT")


def test_configured_interval_rejects_fast_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADE_COLLECTION_INTERVAL_SECONDS", "30")

    with pytest.raises(RuntimeError, match="at least 60"):
        worker.configured_interval_seconds()


def test_automatic_evaluation_is_disabled_in_default_config() -> None:
    from trade_brain.configuration import TradeBrainConfig

    assert TradeBrainConfig.defaults().automatic_evaluation_enabled is False


def test_collect_once_skips_one_failed_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        async def close(self) -> None:
            return None

    class FakeCollector:
        def __init__(self, client, store) -> None:
            pass

        async def collect(self, experiment_id: str, symbol: str):
            if symbol == "BADUSDT":
                raise worker.BinanceClientError("temporary failure")
            return None

    monkeypatch.setattr(worker, "BinancePublicClient", FakeClient)
    monkeypatch.setattr(worker, "PublicSnapshotCollector", FakeCollector)

    collected = asyncio.run(
        worker.collect_once(InMemorySnapshotStore(), "experiment-1", ("BADUSDT", "BTCUSDT"))
    )

    assert collected == 1
