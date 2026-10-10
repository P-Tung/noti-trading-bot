import pytest
import asyncio

from trade_brain import worker
from trade_brain import decision_worker
from trade_brain.storage import InMemorySnapshotStore


def test_configured_symbols_uses_safe_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRADE_SYMBOLS", raising=False)

    assert worker.configured_symbols() == ("BTCUSDT", "ETHUSDT")


def test_configured_interval_rejects_fast_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADE_COLLECTION_INTERVAL_SECONDS", "30")

    with pytest.raises(RuntimeError, match="at least 60"):
        worker.configured_interval_seconds()


def test_auto_evaluation_is_manual_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRADE_AUTO_EVALUATION_ENABLED", raising=False)

    assert decision_worker._auto_evaluation_enabled() is False


def test_auto_evaluation_requires_explicit_boolean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADE_AUTO_EVALUATION_ENABLED", "yes")

    with pytest.raises(RuntimeError, match="must be true or false"):
        decision_worker._auto_evaluation_enabled()


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
