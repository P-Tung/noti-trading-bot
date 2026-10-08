"""Deterministic paper-trading lifecycle and fill simulation."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from uuid import NAMESPACE_URL, uuid5

from trade_brain.contracts import Profile, QualityStatus, Side, TradeCandidate


class PaperState(StrEnum):
    RECOMMENDED = "RECOMMENDED"
    PENDING_FILL = "PENDING_FILL"
    OPEN = "OPEN"
    CLOSED_TP = "CLOSED_TP"
    CLOSED_SL = "CLOSED_SL"
    CLOSED_TIMEOUT = "CLOSED_TIMEOUT"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class PaperConfig:
    fee_rate: Decimal = Decimal("0.0004")
    slippage_bps: Decimal = Decimal("1")
    recommendation_expiry: timedelta = timedelta(seconds=60)
    bar_minutes: int = 15


@dataclass(frozen=True, slots=True)
class MarketQuote:
    observed_at: datetime
    bid: Decimal
    ask: Decimal
    mark: Decimal
    funding_rate: Decimal | None = None
    funding_time: datetime | None = None
    quality_status: QualityStatus = QualityStatus.VALID


@dataclass(slots=True)
class PaperAccount:
    """Independent paper account state for one risk profile."""

    profile: Profile
    initial_equity: Decimal
    current_equity: Decimal
    realized_pnl: Decimal = Decimal("0")


@dataclass(slots=True)
class PaperRecommendation:
    recommendation_id: str
    profile: Profile
    candidate: TradeCandidate
    quantity: Decimal
    emitted_at: datetime
    expires_at: datetime
    state: PaperState = PaperState.RECOMMENDED
    entry_fill: Decimal | None = None
    opened_at: datetime | None = None
    exit_fill: Decimal | None = None
    closed_at: datetime | None = None
    outcome: PaperState | None = None
    net_pnl: Decimal | None = None
    net_r: Decimal | None = None
    funding_pnl: Decimal = Decimal("0")
    last_funding_time: datetime | None = None
    last_mark: Decimal | None = None
    last_quote_at: datetime | None = None
    data_quality: QualityStatus = QualityStatus.VALID


class PaperTradingError(RuntimeError):
    """Raised when a paper lifecycle transition is invalid."""


class PaperTradingEngine:
    """Simulate market fills and lifecycle outcomes without real orders."""

    def __init__(self, config: PaperConfig = PaperConfig()) -> None:
        self._config = config
        self._recommendations: dict[str, PaperRecommendation] = {}
        self._setup_keys: set[tuple[Profile, str]] = set()
        self._accounts = {
            profile: PaperAccount(profile, Decimal("10000"), Decimal("10000"))
            for profile in Profile
        }

    def recommend(
        self,
        candidate: TradeCandidate,
        quantity: Decimal,
        emitted_at: datetime,
    ) -> PaperRecommendation:
        """Create one recommendation per profile and setup lifecycle."""
        if not candidate.eligible:
            raise PaperTradingError("cannot paper trade an ineligible candidate")
        if quantity <= 0:
            raise PaperTradingError("paper quantity must be positive")
        key = (candidate.profile, candidate.setup_id)
        if key in self._setup_keys:
            raise PaperTradingError("duplicate paper recommendation for profile and setup")
        recommendation = PaperRecommendation(
            recommendation_id=str(
                uuid5(
                    NAMESPACE_URL,
                    f"trade-v1-paper:{candidate.profile.value}:{candidate.setup_id}:{candidate.candidate_id}",
                )
            ),
            profile=candidate.profile,
            candidate=candidate,
            quantity=quantity,
            emitted_at=emitted_at,
            expires_at=emitted_at + self._config.recommendation_expiry,
        )
        self._recommendations[recommendation.recommendation_id] = recommendation
        self._setup_keys.add(key)
        return recommendation

    def fill(self, recommendation_id: str, quote: MarketQuote) -> PaperRecommendation:
        """Simulate a market fill using current bid/ask plus slippage."""
        recommendation = self._get(recommendation_id)
        if recommendation.state is not PaperState.RECOMMENDED:
            raise PaperTradingError("only a new recommendation can be filled")
        _validate_quote(quote)
        if quote.observed_at < recommendation.emitted_at:
            raise PaperTradingError("paper fill cannot occur before recommendation emission")
        if quote.observed_at > recommendation.expires_at:
            recommendation.state = PaperState.EXPIRED
            return recommendation
        recommendation.data_quality = _merge_quality(recommendation.data_quality, quote.quality_status)
        if recommendation.data_quality in {QualityStatus.AMBIGUOUS, QualityStatus.INVALID}:
            return recommendation
        recommendation.state = PaperState.PENDING_FILL
        recommendation.entry_fill = _entry_fill(recommendation.candidate.side, quote, self._config.slippage_bps)
        recommendation.opened_at = quote.observed_at
        recommendation.last_mark = quote.mark
        recommendation.last_quote_at = quote.observed_at
        recommendation.state = PaperState.OPEN
        return recommendation

    def mark(self, recommendation_id: str, quote: MarketQuote) -> PaperRecommendation:
        """Close an open position when TP, SL, or timeout is reached."""
        recommendation = self._get(recommendation_id)
        if recommendation.state is not PaperState.OPEN:
            return recommendation
        if recommendation.opened_at is None or recommendation.entry_fill is None:
            raise PaperTradingError("open recommendation has no entry fill")
        _validate_quote(quote)
        if quote.observed_at < recommendation.opened_at:
            raise PaperTradingError("paper mark cannot occur before position open")
        recommendation.last_mark = quote.mark
        recommendation.last_quote_at = quote.observed_at
        recommendation.data_quality = _merge_quality(recommendation.data_quality, quote.quality_status)
        _apply_funding(recommendation, quote)
        if recommendation.data_quality in {QualityStatus.AMBIGUOUS, QualityStatus.INVALID}:
            return recommendation
        deadline = recommendation.opened_at + timedelta(
            minutes=recommendation.candidate.horizon_bars * self._config.bar_minutes
        )
        reason = _close_reason(recommendation, quote, deadline)
        if reason is None:
            return recommendation
        exit_fill = _exit_fill(recommendation.candidate.side, quote, self._config.slippage_bps)
        self._close(recommendation, reason, exit_fill, quote.observed_at)
        return recommendation

    def get(self, recommendation_id: str) -> PaperRecommendation:
        """Return a paper recommendation by identifier."""
        return self._get(recommendation_id)

    def list_recommendations(self) -> list[PaperRecommendation]:
        """Return recommendations in emission order for the paper journal."""
        return sorted(self._recommendations.values(), key=lambda item: item.emitted_at, reverse=True)

    def restore(self, recommendation: PaperRecommendation) -> None:
        """Restore a persisted recommendation without creating a new lifecycle event."""
        if recommendation.recommendation_id in self._recommendations:
            return
        key = (recommendation.profile, recommendation.candidate.setup_id)
        if key in self._setup_keys:
            raise PaperTradingError("duplicate persisted paper recommendation")
        self._recommendations[recommendation.recommendation_id] = recommendation
        self._setup_keys.add(key)

    def find_by_setup(self, profile: Profile, setup_id: str) -> PaperRecommendation | None:
        """Find an existing recommendation for an idempotent setup lifecycle."""
        for recommendation in self._recommendations.values():
            if recommendation.profile is profile and recommendation.candidate.setup_id == setup_id:
                return recommendation
        return None

    def get_account(self, profile: Profile) -> PaperAccount:
        """Return one independent paper account."""
        return self._accounts[profile]

    def equity(self, profile: Profile) -> Decimal:
        """Return realized plus current unrealized paper equity."""
        account = self._accounts[profile]
        return account.current_equity + sum(
            (
                self._unrealized_pnl(recommendation)
                for recommendation in self._recommendations.values()
                if recommendation.profile is profile and recommendation.state is PaperState.OPEN
            ),
            Decimal("0"),
        )

    def _unrealized_pnl(self, recommendation: PaperRecommendation) -> Decimal:
        if recommendation.entry_fill is None or recommendation.last_mark is None:
            return Decimal("0")
        if recommendation.candidate.side is Side.LONG:
            gross = recommendation.quantity * (recommendation.last_mark - recommendation.entry_fill)
        else:
            gross = recommendation.quantity * (recommendation.entry_fill - recommendation.last_mark)
        return gross + recommendation.funding_pnl

    def list_accounts(self) -> list[PaperAccount]:
        """Return all profile accounts in stable profile order."""
        return [self._accounts[profile] for profile in Profile]

    def open_risk_pct(self, profile: Profile) -> Decimal:
        """Return open stop risk as a fraction of the profile equity."""
        account = self._accounts[profile]
        if account.current_equity <= 0:
            return Decimal("1")
        open_risk = sum(
            (
                recommendation.quantity
                * abs(recommendation.entry_fill - Decimal(str(recommendation.candidate.stop_price)))
                for recommendation in self._recommendations.values()
                if recommendation.profile is profile
                and recommendation.state is PaperState.OPEN
                and recommendation.entry_fill is not None
            ),
            Decimal("0"),
        )
        return open_risk / account.current_equity

    def drawdown_pcts(self, profile: Profile, as_of: datetime) -> tuple[Decimal, Decimal]:
        """Return daily and rolling drawdown fractions from closed paper trades."""
        account = self._accounts[profile]
        closed = sorted(
            (
                recommendation
                for recommendation in self._recommendations.values()
                if recommendation.profile is profile
                and recommendation.net_pnl is not None
                and recommendation.closed_at is not None
            ),
            key=lambda recommendation: recommendation.closed_at,
        )
        daily_pnl = sum(
            (
                recommendation.net_pnl
                for recommendation in closed
                if recommendation.closed_at is not None
                and recommendation.closed_at.date() == as_of.date()
            ),
            Decimal("0"),
        )
        open_unrealized = sum(
            (
                self._unrealized_pnl(recommendation)
                for recommendation in self._recommendations.values()
                if recommendation.profile is profile and recommendation.state is PaperState.OPEN
            ),
            Decimal("0"),
        )
        daily_pnl += open_unrealized
        daily_drawdown = max(Decimal("0"), -daily_pnl / account.initial_equity)
        equity = account.initial_equity
        peak = equity
        rolling_drawdown = Decimal("0")
        for recommendation in closed:
            equity += recommendation.net_pnl or Decimal("0")
            peak = max(peak, equity)
            if peak > 0:
                rolling_drawdown = max(rolling_drawdown, (peak - equity) / peak)
        equity += open_unrealized
        if peak > 0:
            rolling_drawdown = max(rolling_drawdown, (peak - equity) / peak)
        return daily_drawdown, rolling_drawdown

    def restore_account(self, account: PaperAccount) -> None:
        """Restore persisted equity without replaying the trade lifecycle."""
        self._accounts[account.profile] = account

    def _get(self, recommendation_id: str) -> PaperRecommendation:
        try:
            return self._recommendations[recommendation_id]
        except KeyError as error:
            raise PaperTradingError("unknown paper recommendation") from error

    def _close(
        self,
        recommendation: PaperRecommendation,
        reason: PaperState,
        exit_fill: Decimal,
        closed_at: datetime,
    ) -> None:
        entry_fill = recommendation.entry_fill
        if entry_fill is None:
            raise PaperTradingError("cannot close without entry fill")
        side = recommendation.candidate.side
        gross_pnl = (
            recommendation.quantity * (exit_fill - entry_fill)
            if side is Side.LONG
            else recommendation.quantity * (entry_fill - exit_fill)
        )
        fees = (entry_fill + exit_fill) * recommendation.quantity * self._config.fee_rate
        net_pnl = gross_pnl - fees + recommendation.funding_pnl
        initial_risk = recommendation.quantity * abs(entry_fill - Decimal(str(recommendation.candidate.stop_price)))
        recommendation.exit_fill = exit_fill
        recommendation.closed_at = closed_at
        recommendation.outcome = reason
        recommendation.net_pnl = net_pnl
        recommendation.net_r = net_pnl / initial_risk if initial_risk > 0 else None
        recommendation.state = reason
        account = self._accounts[recommendation.profile]
        account.current_equity += net_pnl
        account.realized_pnl += net_pnl


def _entry_fill(side: Side, quote: MarketQuote, slippage_bps: Decimal) -> Decimal:
    factor = Decimal("1") + slippage_bps / Decimal("10000")
    return quote.ask * factor if side is Side.LONG else quote.bid / factor


def _validate_quote(quote: MarketQuote) -> None:
    if quote.bid <= 0 or quote.ask <= 0 or quote.mark <= 0:
        raise PaperTradingError("market quote prices must be positive")
    if quote.ask < quote.bid:
        raise PaperTradingError("market quote ask must be >= bid")
    if quote.funding_rate is not None and quote.funding_time is None:
        raise PaperTradingError("funding time is required when funding rate is supplied")
    if quote.funding_time is not None and quote.funding_time > quote.observed_at:
        raise PaperTradingError("funding event cannot occur after quote observation")


def _merge_quality(current: QualityStatus, incoming: QualityStatus) -> QualityStatus:
    """Keep the most conservative quality observed during the lifecycle."""
    rank = {
        QualityStatus.VALID: 0,
        QualityStatus.DEGRADED: 1,
        QualityStatus.AMBIGUOUS: 2,
        QualityStatus.INVALID: 3,
    }
    return current if rank[current] >= rank[incoming] else incoming


def _apply_funding(recommendation: PaperRecommendation, quote: MarketQuote) -> None:
    """Apply one funding event at most once to an open paper position."""
    if quote.funding_rate is None or quote.funding_time is None:
        return
    if recommendation.opened_at is None or quote.funding_time <= recommendation.opened_at:
        return
    if recommendation.last_funding_time is not None and quote.funding_time <= recommendation.last_funding_time:
        return
    notional = recommendation.quantity * quote.mark
    signed_funding = -notional * quote.funding_rate
    if recommendation.candidate.side is Side.SHORT:
        signed_funding = -signed_funding
    recommendation.funding_pnl += signed_funding
    recommendation.last_funding_time = quote.funding_time


def _exit_fill(side: Side, quote: MarketQuote, slippage_bps: Decimal) -> Decimal:
    factor = Decimal("1") + slippage_bps / Decimal("10000")
    return quote.bid / factor if side is Side.LONG else quote.ask * factor


def _close_reason(
    recommendation: PaperRecommendation,
    quote: MarketQuote,
    deadline: datetime,
) -> PaperState | None:
    target = Decimal(str(recommendation.candidate.target_price))
    stop = Decimal(str(recommendation.candidate.stop_price))
    if recommendation.candidate.side is Side.LONG:
        if quote.bid <= stop:
            return PaperState.CLOSED_SL
        if quote.bid >= target:
            return PaperState.CLOSED_TP
    else:
        if quote.ask >= stop:
            return PaperState.CLOSED_SL
        if quote.ask <= target:
            return PaperState.CLOSED_TP
    return PaperState.CLOSED_TIMEOUT if quote.observed_at >= deadline else None
