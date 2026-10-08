import asyncio
import json
import unittest
from datetime import datetime, timezone

import httpx

from trade_brain.claude import ClaudeSelector
from trade_brain.contracts import ClaudeDecisionBatch, FeatureValue, MarketSnapshot, QualityStatus


def make_snapshot() -> MarketSnapshot:
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return MarketSnapshot(
        snapshot_id="snapshot-1",
        experiment_id="experiment-1",
        symbol="BTCUSDT",
        decision_time=timestamp,
        expires_at=timestamp.replace(second=59),
        quality_status=QualityStatus.VALID,
        data_mode="PRICE_ONLY",
        feature_version="features-v1",
        strategy_version="strategies-v1",
        policy_version="policies-v1",
        features={
            "close": FeatureValue(
                value=100,
                unit="USDT",
                observed_at=timestamp,
                available_at=timestamp,
                quality_status=QualityStatus.VALID,
            )
        },
    )


def valid_response() -> dict[str, object]:
    decisions = []
    for profile in ("PROACTIVE", "BALANCED", "CAUTIOUS"):
        decisions.append(
            {
                "snapshot_id": "snapshot-1",
                "profile": profile,
                "decision": "NO_TRADE",
                "selected_candidate_id": None,
                "watch_candidate_id": None,
                "condition_ids": [],
                "reason_codes": ["NO_ELIGIBLE_CANDIDATE"],
                "evidence_ids": [],
                "summary_vi": "Chưa có phương án đủ điều kiện.",
            }
        )
    return {"content": [{"type": "text", "text": json.dumps({"snapshot_id": "snapshot-1", "decisions": decisions})}]}


class ClaudeTests(unittest.TestCase):
    def test_parses_structured_decision_batch(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=valid_response())

        async def run() -> tuple[ClaudeDecisionBatch, dict[str, object] | None]:
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://test")
            selector = ClaudeSelector("test-key", "test-model", client)
            result = await selector.select(make_snapshot(), [])
            audit = selector.last_audit
            await client.aclose()
            return result, audit

        result, audit = asyncio.run(run())
        self.assertEqual(len(result.decisions), 3)
        self.assertEqual(audit["model"], "test-model")
        self.assertEqual(audit["prompt_version"], "trade-v1-claude-prompt-v1")
        self.assertEqual(len(audit["payload_hash"]), 64)
        self.assertGreaterEqual(audit["latency_ms"], 0)

    def test_malformed_response_fails_closed(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"content": [{"type": "text", "text": "not-json"}]})

        async def run() -> ClaudeDecisionBatch:
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://test")
            selector = ClaudeSelector("test-key", "test-model", client)
            return await selector.select(make_snapshot(), [])

        result = asyncio.run(run())
        self.assertTrue(all(decision.decision.value == "NO_TRADE" for decision in result.decisions))
        self.assertTrue(all("DECISION_SERVICE_ERROR" in decision.reason_codes for decision in result.decisions))


if __name__ == "__main__":
    unittest.main()
