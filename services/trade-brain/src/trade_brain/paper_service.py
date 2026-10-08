"""Bridge validated decisions into the paper-trading lifecycle."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from trade_brain.contracts import Decision, Profile, TradeCandidate
from trade_brain.orchestration import DecisionCycleResult
from trade_brain.paper import PaperRecommendation, PaperTradingEngine


@dataclass(frozen=True, slots=True)
class PaperDecisionResult:
    """Paper recommendations created from one validated decision cycle."""

    recommendations: tuple[PaperRecommendation, ...]
    skipped: dict[Profile, str]


def create_paper_recommendations(
    cycle: DecisionCycleResult,
    engine: PaperTradingEngine,
    emitted_at: datetime,
) -> PaperDecisionResult:
    """Create paper recommendations only for validated LONG or SHORT decisions."""
    candidates = {candidate.candidate_id: candidate for candidate in cycle.candidates}
    recommendations: list[PaperRecommendation] = []
    skipped: dict[Profile, str] = {}
    for decision in cycle.decisions:
        if decision.decision not in (Decision.LONG, Decision.SHORT):
            skipped[decision.profile] = f"DECISION_{decision.decision.value}"
            continue
        if decision.selected_candidate_id is None:
            skipped[decision.profile] = "SELECTED_CANDIDATE_MISSING"
            continue
        candidate = candidates.get(decision.selected_candidate_id)
        risk_result = cycle.risk_results.get(decision.selected_candidate_id)
        if candidate is None or risk_result is None or not candidate.eligible:
            skipped[decision.profile] = "CANDIDATE_NOT_PAPER_ELIGIBLE"
            continue
        if risk_result.quantity_allowed <= 0:
            skipped[decision.profile] = "QUANTITY_UNAVAILABLE"
            continue
        if engine.find_by_setup(candidate.profile, candidate.setup_id) is not None:
            skipped[decision.profile] = "DUPLICATE_EXISTING_RECOMMENDATION"
            continue
        recommendation = engine.recommend(
            candidate,
            Decimal(str(risk_result.quantity_allowed)),
            emitted_at,
        )
        recommendations.append(recommendation)
    return PaperDecisionResult(tuple(recommendations), skipped)
