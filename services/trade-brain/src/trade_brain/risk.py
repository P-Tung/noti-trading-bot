"""Independent position-sizing and profile risk gates."""

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from trade_brain.contracts import PaperMode, Profile, Side, StatisticsStatus, TradeCandidate


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    quality_minimum: float
    expectancy_minimum_r: float
    effective_sample_minimum: int
    risk_per_trade_pct: float
    max_open_risk_pct: float
    max_cluster_risk_pct: float
    daily_drawdown_stop_pct: float
    rolling_drawdown_stop_pct: float


POLICIES: dict[Profile, RiskPolicy] = {
    Profile.PROACTIVE: RiskPolicy(65, 0.10, 150, 0.005, 0.020, 0.010, 0.020, 0.080),
    Profile.BALANCED: RiskPolicy(75, 0.15, 250, 0.0035, 0.015, 0.0075, 0.015, 0.060),
    Profile.CAUTIOUS: RiskPolicy(85, 0.15, 400, 0.002, 0.010, 0.005, 0.010, 0.040),
}


@dataclass(frozen=True, slots=True)
class RiskContext:
    equity_usdt: float
    open_risk_pct: float
    cluster_risk_pct: float
    daily_drawdown_pct: float
    rolling_drawdown_pct: float
    available_margin_usdt: float
    liquidity_quantity_cap: float | None = None
    paper_mode: PaperMode = PaperMode.VERIFIED_PAPER


@dataclass(frozen=True, slots=True)
class CandidateStatistics:
    quality_score: float | None
    net_expectancy_r: float | None
    expectancy_lower_95_r: float | None
    effective_sample_count: int | None
    cost_estimate_r: float | None


@dataclass(frozen=True, slots=True)
class RiskResult:
    allowed: bool
    codes: tuple[str, ...]
    risk_budget_usdt: float
    quantity_allowed: float


def calculate_quantity(
    risk_budget_usdt: float,
    entry_price: float,
    stop_price: float,
    fee_entry_per_unit: float,
    fee_exit_per_unit: float,
    adverse_slippage_per_unit: float,
    adverse_funding_per_unit: float,
    quantity_step: float,
    minimum_quantity: float = 0.0,
) -> float:
    """Calculate quantity and round down to the exchange step."""
    price_risk = abs(entry_price - stop_price)
    denominator = price_risk + fee_entry_per_unit + fee_exit_per_unit + adverse_slippage_per_unit + adverse_funding_per_unit
    if denominator <= 0 or quantity_step <= 0:
        return 0.0
    raw_quantity = Decimal(str(risk_budget_usdt)) / Decimal(str(denominator))
    step = Decimal(str(quantity_step))
    rounded_quantity = (raw_quantity / step).to_integral_value(rounding=ROUND_DOWN) * step
    if minimum_quantity > 0 and rounded_quantity < Decimal(str(minimum_quantity)):
        return 0.0
    return float(rounded_quantity)


def evaluate_candidate_risk(
    candidate: TradeCandidate,
    statistics: CandidateStatistics,
    context: RiskContext,
    fee_entry_per_unit: float,
    fee_exit_per_unit: float,
    adverse_slippage_per_unit: float,
    adverse_funding_per_unit: float,
    quantity_step: float,
    minimum_quantity: float = 0.0,
    price_tick: float = 0.0,
    policy: RiskPolicy | None = None,
) -> RiskResult:
    """Apply hard profile gates before Claude can select the candidate."""
    active_policy = policy or POLICIES[candidate.profile]
    codes: list[str] = []
    if not candidate.eligible:
        codes.append("CANDIDATE_NOT_ELIGIBLE")
    _check_price_plan(candidate, codes)
    _check_price_tick(candidate, price_tick, codes)
    if context.paper_mode is PaperMode.VERIFIED_PAPER:
        _check_statistics(candidate, statistics, active_policy, codes)
    _check_drawdown(context, active_policy, codes)
    risk_budget = context.equity_usdt * active_policy.risk_per_trade_pct
    candidate_risk_pct = risk_budget / context.equity_usdt if context.equity_usdt > 0 else 1.0
    if context.open_risk_pct + candidate_risk_pct > active_policy.max_open_risk_pct:
        codes.append("OPEN_RISK_LIMIT")
    if context.cluster_risk_pct + candidate_risk_pct > active_policy.max_cluster_risk_pct:
        codes.append("CLUSTER_RISK_LIMIT")
    if context.available_margin_usdt <= 0:
        codes.append("MARGIN_UNAVAILABLE")

    quantity = calculate_quantity(
        risk_budget,
        candidate.entry_estimate,
        candidate.stop_price,
        fee_entry_per_unit,
        fee_exit_per_unit,
        adverse_slippage_per_unit,
        adverse_funding_per_unit,
        quantity_step,
        minimum_quantity,
    )
    if context.liquidity_quantity_cap is not None:
        quantity = min(quantity, context.liquidity_quantity_cap)
    if quantity <= 0:
        codes.append("QUANTITY_UNAVAILABLE")
    return RiskResult(not codes, tuple(codes), risk_budget, quantity)


def _check_price_tick(candidate: TradeCandidate, price_tick: float, codes: list[str]) -> None:
    """Reject prices that cannot be represented by the exchange price filter."""
    if price_tick <= 0:
        return
    tick = Decimal(str(price_tick))
    prices = (candidate.entry_estimate, candidate.stop_price, candidate.target_price)
    if any(Decimal(str(price)) % tick != 0 for price in prices):
        codes.append("PRICE_TICK_INVALID")


def _check_price_plan(candidate: TradeCandidate, codes: list[str]) -> None:
    """Enforce the independent stop and target direction invariants."""
    if candidate.side is Side.LONG:
        if candidate.stop_price >= candidate.entry_estimate:
            codes.append("STOP_WRONG_SIDE")
        if candidate.target_price <= candidate.entry_estimate:
            codes.append("TARGET_WRONG_SIDE")
        return
    if candidate.stop_price <= candidate.entry_estimate:
        codes.append("STOP_WRONG_SIDE")
    if candidate.target_price >= candidate.entry_estimate:
        codes.append("TARGET_WRONG_SIDE")


def _check_statistics(
    candidate: TradeCandidate,
    statistics: CandidateStatistics,
    policy: RiskPolicy,
    codes: list[str],
) -> None:
    if candidate.statistics_status is not StatisticsStatus.VERIFIED:
        codes.append("STATISTICS_NOT_VERIFIED")
    if statistics.quality_score is None or statistics.quality_score < policy.quality_minimum:
        codes.append("QUALITY_THRESHOLD")
    if statistics.net_expectancy_r is None or statistics.net_expectancy_r < policy.expectancy_minimum_r:
        codes.append("EXPECTANCY_THRESHOLD")
    if statistics.expectancy_lower_95_r is None or statistics.expectancy_lower_95_r <= 0:
        codes.append("EXPECTANCY_CONFIDENCE_BOUND")
    if statistics.effective_sample_count is None or statistics.effective_sample_count < policy.effective_sample_minimum:
        codes.append("EFFECTIVE_SAMPLE_THRESHOLD")
    if statistics.cost_estimate_r is None or statistics.cost_estimate_r < 0:
        codes.append("COST_ESTIMATE_MISSING")


def _check_drawdown(context: RiskContext, policy: RiskPolicy, codes: list[str]) -> None:
    if context.daily_drawdown_pct >= policy.daily_drawdown_stop_pct:
        codes.append("DAILY_DRAWDOWN_STOP")
    if context.rolling_drawdown_pct >= policy.rolling_drawdown_stop_pct:
        codes.append("ROLLING_DRAWDOWN_STOP")
