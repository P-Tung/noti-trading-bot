"""Fail-closed decision-cycle orchestration for paper trading."""

from dataclasses import dataclass, field
from typing import Protocol

from trade_brain.claude import ClaudeSelector
from trade_brain.contracts import (
    ClaudeDecision,
    ClaudeDecisionBatch,
    Decision,
    MarketSnapshot,
    Profile,
    QualityStatus,
    TradeCandidate,
)
from trade_brain.risk import CandidateStatistics, POLICIES, RiskContext, RiskPolicy, RiskResult, evaluate_candidate_risk
from trade_brain.validation import DecisionValidationError, validate_decision


class DecisionSelector(Protocol):
    async def select(
        self,
        snapshot: MarketSnapshot,
        candidates: list[TradeCandidate],
    ) -> ClaudeDecisionBatch:
        """Select from backend-issued candidates."""


@dataclass(frozen=True, slots=True)
class CandidateRiskInput:
    """Risk evidence and execution-cost assumptions for one candidate."""

    statistics: CandidateStatistics
    context: RiskContext
    fee_entry_per_unit: float
    fee_exit_per_unit: float
    adverse_slippage_per_unit: float
    adverse_funding_per_unit: float
    quantity_step: float
    minimum_quantity: float = 0.0
    price_tick: float = 0.0


@dataclass(frozen=True, slots=True)
class DecisionCycleResult:
    """Auditable output of one snapshot decision cycle."""

    snapshot_id: str
    candidates: tuple[TradeCandidate, ...]
    risk_results: dict[str, RiskResult]
    decisions: tuple[ClaudeDecision, ...]
    validation_errors: dict[Profile, str]
    claude_audit: dict[str, object] | None = None
    gate_audit: dict[str, object] = field(default_factory=dict)


async def run_decision_cycle(
    snapshot: MarketSnapshot,
    candidates: list[TradeCandidate],
    risk_inputs: dict[str, CandidateRiskInput],
    selector: DecisionSelector | ClaudeSelector,
    policies: dict[Profile, RiskPolicy] | None = None,
    pipeline_audit: dict[str, object] | None = None,
) -> DecisionCycleResult:
    """Apply risk gates, call Claude, then validate every decision independently."""
    risk_results: dict[str, RiskResult] = {}
    active_policies = policies or POLICIES
    gated_candidates: list[TradeCandidate] = []
    for candidate in candidates:
        if snapshot.quality_status in {QualityStatus.INVALID, QualityStatus.AMBIGUOUS}:
            risk_results[candidate.candidate_id] = RiskResult(
                allowed=False,
                codes=("SNAPSHOT_DATA_INVALID",),
                risk_budget_usdt=0.0,
                quantity_allowed=0.0,
            )
            gated_candidates.append(candidate.model_copy(update={"eligible": False}))
            continue
        risk_input = risk_inputs.get(candidate.candidate_id)
        if risk_input is None:
            gated_candidate = candidate.model_copy(update={"eligible": False})
            risk_results[candidate.candidate_id] = _missing_risk_result()
        else:
            risk_result = evaluate_candidate_risk(
                candidate,
                risk_input.statistics,
                risk_input.context,
                risk_input.fee_entry_per_unit,
                risk_input.fee_exit_per_unit,
                risk_input.adverse_slippage_per_unit,
                risk_input.adverse_funding_per_unit,
                risk_input.quantity_step,
                risk_input.minimum_quantity,
                risk_input.price_tick,
                policy=active_policies[candidate.profile],
            )
            risk_results[candidate.candidate_id] = risk_result
            gated_candidate = candidate.model_copy(update={"eligible": risk_result.allowed})
        gated_candidates.append(gated_candidate)

    if not any(candidate.eligible for candidate in gated_candidates):
        return DecisionCycleResult(
            snapshot_id=snapshot.snapshot_id,
            candidates=tuple(gated_candidates),
            risk_results=risk_results,
            decisions=tuple(_no_trade_without_ai(snapshot.snapshot_id, profile) for profile in Profile),
            validation_errors={},
            claude_audit=None,
            gate_audit=_gate_audit(pipeline_audit, gated_candidates, risk_results, False),
        )

    batch = await selector.select(snapshot, gated_candidates)
    claude_audit = getattr(selector, "last_audit", None)
    if batch.snapshot_id != snapshot.snapshot_id:
        error = "Claude decision batch snapshot does not match requested snapshot"
        return DecisionCycleResult(
            snapshot_id=snapshot.snapshot_id,
            candidates=tuple(gated_candidates),
            risk_results=risk_results,
            decisions=tuple(_safe_no_trade(snapshot.snapshot_id, profile, error) for profile in Profile),
            validation_errors={profile: error for profile in Profile},
            claude_audit=claude_audit,
            gate_audit=_gate_audit(pipeline_audit, gated_candidates, risk_results, True),
        )
    candidates_by_id = {candidate.candidate_id: candidate for candidate in gated_candidates}
    validated_decisions: list[ClaudeDecision] = []
    validation_errors: dict[Profile, str] = {}
    for decision in batch.decisions:
        try:
            validated_decisions.append(validate_decision(decision, candidates_by_id))
        except DecisionValidationError as error:
            validation_errors[decision.profile] = str(error)
            validated_decisions.append(_safe_no_trade(snapshot.snapshot_id, decision.profile, str(error)))

    return DecisionCycleResult(
        snapshot_id=snapshot.snapshot_id,
        candidates=tuple(gated_candidates),
        risk_results=risk_results,
        decisions=tuple(validated_decisions),
        validation_errors=validation_errors,
        claude_audit=claude_audit,
        gate_audit=_gate_audit(pipeline_audit, gated_candidates, risk_results, True),
    )


def _gate_audit(
    pipeline_audit: dict[str, object] | None,
    candidates: list[TradeCandidate],
    risk_results: dict[str, RiskResult],
    ai_called: bool,
) -> dict[str, object]:
    audit = dict(pipeline_audit or {})
    audit.update(
        {
            "eligible_count": sum(candidate.eligible for candidate in candidates),
            "risk_allowed_count": sum(result.allowed for result in risk_results.values()),
            "ai_called": ai_called,
        }
    )
    return audit


def _missing_risk_result() -> RiskResult:
    return RiskResult(
        allowed=False,
        codes=("RISK_INPUT_MISSING",),
        risk_budget_usdt=0.0,
        quantity_allowed=0.0,
    )


def _no_trade_without_ai(snapshot_id: str, profile: Profile) -> ClaudeDecision:
    """Represent a deterministic no-op without spending an AI request."""
    return ClaudeDecision(
        snapshot_id=snapshot_id,
        profile=profile,
        decision=Decision.NO_TRADE,
        reason_codes=["NO_ELIGIBLE_CANDIDATE"],
        summary_vi="Chưa có ứng viên đủ điều kiện máy, chưa gọi Claude.",
    )


def _safe_no_trade(snapshot_id: str, profile: Profile, reason: str) -> ClaudeDecision:
    return ClaudeDecision(
        snapshot_id=snapshot_id,
        profile=profile,
        decision=Decision.NO_TRADE,
        reason_codes=["DECISION_CONTRACT_ERROR"],
        summary_vi=f"Đã chặn quyết định không hợp lệ: {reason}"[:500],
    )
