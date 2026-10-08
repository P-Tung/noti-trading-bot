"""Fail-closed Claude candidate selection using structured JSON output."""

import hashlib
import json
import os
import time

import httpx

from trade_brain.contracts import (
    ClaudeDecision,
    ClaudeDecisionBatch,
    Decision,
    MarketSnapshot,
    Profile,
    TradeCandidate,
)
from trade_brain.conditions import WAIT_CONDITION_REGISTRY, allowed_wait_condition_ids


class ClaudeSelectorError(RuntimeError):
    """Raised when Claude cannot produce a valid decision batch."""


class ClaudeSelector:
    """Call Claude only after backend candidate and risk filtering."""

    def __init__(
        self,
        api_key: str,
        model: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key or not model:
            raise ValueError("api_key and model are required")
        self._api_key = api_key
        self._model = model
        self._client = client or httpx.AsyncClient(base_url="https://api.anthropic.com", timeout=30.0)
        self._owns_client = client is None
        self._last_audit: dict[str, object] | None = None

    @property
    def last_audit(self) -> dict[str, object] | None:
        """Return metadata for the most recent request without exposing secrets."""
        return self._last_audit

    @classmethod
    def from_environment(cls) -> "ClaudeSelector":
        """Create a selector from server-only environment variables."""
        return cls(os.environ.get("ANTHROPIC_API_KEY", ""), os.environ.get("CLAUDE_MODEL", ""))

    async def close(self) -> None:
        """Close the owned HTTP client."""
        if self._owns_client:
            await self._client.aclose()

    async def select(
        self,
        snapshot: MarketSnapshot,
        candidates: list[TradeCandidate],
    ) -> ClaudeDecisionBatch:
        """Select candidates or return three fail-closed NO_TRADE decisions."""
        try:
            payload = await self._request(snapshot, candidates)
            return ClaudeDecisionBatch.model_validate_json(payload)
        except (ClaudeSelectorError, httpx.HTTPError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            return _no_trade_batch(snapshot.snapshot_id, f"Claude service error: {error}")

    async def _request(self, snapshot: MarketSnapshot, candidates: list[TradeCandidate]) -> str:
        request_body = {
            "model": self._model,
            "max_tokens": 2048,
            "system": _system_prompt(),
            "messages": [{"role": "user", "content": _user_prompt(snapshot, candidates)}],
            "output_config": {"format": {"type": "json_schema", "schema": _decision_schema()}},
        }
        payload_bytes = json.dumps(
            request_body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        started_at = time.perf_counter()
        try:
            response = await self._client.post(
                "/v1/messages",
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=request_body,
            )
            response.raise_for_status()
            body = response.json()
            return _extract_text(body)
        finally:
            self._last_audit = {
                "model": self._model,
                "prompt_version": CLAUDE_PROMPT_VERSION,
                "schema_version": CLAUDE_SCHEMA_VERSION,
                "payload_hash": hashlib.sha256(payload_bytes).hexdigest(),
                "latency_ms": round((time.perf_counter() - started_at) * 1000, 3),
            }


CLAUDE_PROMPT_VERSION = "trade-v1-claude-prompt-v1"
CLAUDE_SCHEMA_VERSION = "trade-v1-claude-schema-v1"


def _system_prompt() -> str:
    return (
        "You are a Trade V1 candidate evaluator. Use only the supplied snapshot and candidates. "
        "Never invent prices, probabilities, risk values, or candidates. Select only eligible candidates. "
        "Return exactly one decision for each profile. Use WAIT only with a supplied watch candidate "
        f"and one or more allowed condition IDs: {', '.join(allowed_wait_condition_ids())}. "
        "Use NO_TRADE when evidence or candidates are insufficient. "
        "When a candidate is marked RESEARCH_ONLY, treat it as an explicitly unverified paper study: "
        "do not invent missing probabilities or expectancy and make no claim that an edge is proven."
    )


def _user_prompt(snapshot: MarketSnapshot, candidates: list[TradeCandidate]) -> str:
    eligible = [candidate.model_dump(mode="json") for candidate in candidates]
    return json.dumps(
        {
            "snapshot": snapshot.model_dump(mode="json"),
            "candidates": eligible,
            "wait_condition_registry": WAIT_CONDITION_REGISTRY,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _decision_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["snapshot_id", "decisions"],
        "properties": {
            "snapshot_id": {"type": "string"},
            "decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "snapshot_id",
                        "profile",
                        "decision",
                        "selected_candidate_id",
                        "watch_candidate_id",
                        "condition_ids",
                        "reason_codes",
                        "evidence_ids",
                        "summary_vi",
                    ],
                    "properties": {
                        "snapshot_id": {"type": "string"},
                        "profile": {"type": "string", "enum": [profile.value for profile in Profile]},
                        "decision": {"type": "string", "enum": [decision.value for decision in Decision]},
                        "selected_candidate_id": {"type": ["string", "null"]},
                        "watch_candidate_id": {"type": ["string", "null"]},
                        "condition_ids": {"type": "array", "items": {"type": "string"}},
                        "reason_codes": {"type": "array", "items": {"type": "string"}},
                        "evidence_ids": {"type": "array", "items": {"type": "string"}},
                        "summary_vi": {"type": "string"},
                    },
                },
            },
        },
    }


def _extract_text(body: object) -> str:
    if not isinstance(body, dict) or not isinstance(body.get("content"), list):
        raise ClaudeSelectorError("Claude response has no content blocks")
    for block in body["content"]:
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
            return block["text"]
    raise ClaudeSelectorError("Claude response has no text block")


def _no_trade_batch(snapshot_id: str, error: str) -> ClaudeDecisionBatch:
    decisions = [
        ClaudeDecision(
            snapshot_id=snapshot_id,
            profile=profile,
            decision=Decision.NO_TRADE,
            reason_codes=["DECISION_SERVICE_ERROR"],
            summary_vi=error[:500],
        )
        for profile in Profile
    ]
    return ClaudeDecisionBatch(snapshot_id=snapshot_id, decisions=decisions)
