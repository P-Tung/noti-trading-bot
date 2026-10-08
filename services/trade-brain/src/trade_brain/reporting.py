"""Locked cohort reports for paper recommendations and completed trades."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from decimal import Decimal

from trade_brain.contracts import Profile, QualityStatus
from trade_brain.paper import PaperState


class CohortScope(StrEnum):
    PROFILE_RECOMMENDATIONS = "PROFILE_RECOMMENDATIONS"
    PROFILE_COMPLETED_TRADES = "PROFILE_COMPLETED_TRADES"
    GLOBAL_SETUP_RECOMMENDATIONS = "GLOBAL_SETUP_RECOMMENDATIONS"


@dataclass(frozen=True, slots=True)
class ReportRecord:
    """Event-backed record used to build a stable report."""

    recommendation_id: str
    profile: Profile
    setup_id: str
    emitted_at: datetime
    state: PaperState
    net_pnl: Decimal | None = None
    net_r: Decimal | None = None
    closed_at: datetime | None = None
    opened_at: datetime | None = None
    initial_equity: Decimal = Decimal("10000")
    data_quality: QualityStatus = QualityStatus.VALID


@dataclass(frozen=True, slots=True)
class LockedCohort:
    scope: CohortScope
    profile: Profile | None
    milestone: int
    member_ids: tuple[str, ...]
    status: str


@dataclass(frozen=True, slots=True)
class PaperReport:
    scope: CohortScope
    profile: Profile | None
    milestone: int
    member_ids: tuple[str, ...]
    recommendation_count: int
    filled_count: int
    closed_count: int
    open_count: int
    expired_or_cancelled_count: int
    ambiguous_count: int
    wins: int
    losses: int
    breakeven: int
    net_positive_rate: Decimal | None
    total_net_pnl: Decimal
    total_net_r: Decimal
    max_drawdown: Decimal
    cohort_status: str


class CohortTracker:
    """Maintain append-only records and deterministic 100-member cohorts."""

    def __init__(self, milestone_size: int = 100) -> None:
        if milestone_size <= 0:
            raise ValueError("milestone_size must be positive")
        self._milestone_size = milestone_size
        self._records: dict[str, ReportRecord] = {}
        self._locked: dict[tuple[CohortScope, Profile | None, int], LockedCohort] = {}

    def record(self, record: ReportRecord) -> None:
        """Insert a record once; corrections require a new event upstream."""
        if record.recommendation_id in self._records:
            raise ValueError("report record already exists")
        self._records[record.recommendation_id] = record

    def replace_state(self, record: ReportRecord) -> None:
        """Apply a state update while preserving the recommendation identity."""
        if record.recommendation_id not in self._records:
            raise ValueError("cannot update an unknown report record")
        self._records[record.recommendation_id] = record

    def get_cohort(
        self,
        scope: CohortScope,
        profile: Profile | None,
        milestone: int,
    ) -> LockedCohort | None:
        """Return a cohort once its ordered member set reaches the milestone."""
        if milestone <= 0:
            raise ValueError("milestone must be positive")
        key = (scope, profile, milestone)
        if key in self._locked:
            cohort = self._locked[key]
            status = self._status(cohort.member_ids)
            if status != cohort.status:
                cohort = LockedCohort(cohort.scope, cohort.profile, cohort.milestone, cohort.member_ids, status)
                self._locked[key] = cohort
            return cohort
        records = self._records_for_scope(scope, profile)
        start = (milestone - 1) * self._milestone_size
        end = start + self._milestone_size
        if len(records) < end:
            return None
        members = tuple(record.recommendation_id for record in records[start:end])
        cohort = LockedCohort(scope, profile, milestone, members, self._status(members))
        self._locked[key] = cohort
        return cohort

    def report(
        self,
        scope: CohortScope,
        profile: Profile | None,
        milestone: int,
    ) -> PaperReport | None:
        """Build a report from the locked cohort, including provisional state."""
        cohort = self.get_cohort(scope, profile, milestone)
        if cohort is None:
            return None
        records = [self._records[member_id] for member_id in cohort.member_ids]
        closed = [record for record in records if record.net_pnl is not None]
        wins = sum(record.net_pnl > 0 for record in closed)
        losses = sum(record.net_pnl < 0 for record in closed)
        breakeven = len(closed) - wins - losses
        return PaperReport(
            scope=scope,
            profile=profile,
            milestone=milestone,
            member_ids=cohort.member_ids,
            recommendation_count=len(records),
            filled_count=sum(
                record.state
                in {
                    PaperState.OPEN,
                    PaperState.CLOSED_TP,
                    PaperState.CLOSED_SL,
                    PaperState.CLOSED_TIMEOUT,
                }
                for record in records
            ),
            closed_count=len(closed),
            open_count=sum(record.state is PaperState.OPEN for record in records),
            expired_or_cancelled_count=sum(record.state in {PaperState.EXPIRED, PaperState.CANCELLED} for record in records),
            ambiguous_count=sum(
                record.data_quality in {QualityStatus.AMBIGUOUS, QualityStatus.INVALID}
                for record in records
            ),
            wins=wins,
            losses=losses,
            breakeven=breakeven,
            net_positive_rate=Decimal(wins) / Decimal(len(closed)) if closed else None,
            total_net_pnl=sum((record.net_pnl or Decimal("0") for record in closed), Decimal("0")),
            total_net_r=sum((record.net_r or Decimal("0") for record in closed), Decimal("0")),
            max_drawdown=_max_drawdown(closed),
            cohort_status=cohort.status,
        )

    def available_milestones(
        self,
        scope: CohortScope,
        profile: Profile | None,
    ) -> tuple[int, ...]:
        """Return every fully populated milestone currently available."""
        records = self._records_for_scope(scope, profile)
        count = len(records) // self._milestone_size
        return tuple(range(1, count + 1))

    def _records_for_scope(self, scope: CohortScope, profile: Profile | None) -> list[ReportRecord]:
        sort_key = (
            (lambda record: record.opened_at or record.emitted_at)
            if scope is CohortScope.PROFILE_COMPLETED_TRADES
            else (lambda record: record.emitted_at)
        )
        records = sorted(self._records.values(), key=sort_key)
        if scope is CohortScope.GLOBAL_SETUP_RECOMMENDATIONS:
            seen: set[str] = set()
            unique_records: list[ReportRecord] = []
            for record in records:
                if record.setup_id in seen:
                    continue
                seen.add(record.setup_id)
                unique_records.append(record)
            return unique_records
        if profile is None:
            raise ValueError("profile is required for profile scopes")
        records = [record for record in records if record.profile is profile]
        if scope is CohortScope.PROFILE_COMPLETED_TRADES:
            records = [record for record in records if record.net_pnl is not None]
        return records

    def _status(self, member_ids: tuple[str, ...]) -> str:
        terminal = {PaperState.CLOSED_TP, PaperState.CLOSED_SL, PaperState.CLOSED_TIMEOUT, PaperState.CANCELLED, PaperState.EXPIRED}
        return "FINAL" if all(self._records[member_id].state in terminal for member_id in member_ids) else "PROVISIONAL"


def _max_drawdown(records: list[ReportRecord]) -> Decimal:
    baseline = records[0].initial_equity if records else Decimal("0")
    if baseline <= 0:
        return Decimal("0")
    equity = baseline
    peak = baseline
    drawdown = Decimal("0")
    for record in sorted(records, key=lambda item: item.closed_at or item.emitted_at):
        equity += record.net_pnl or Decimal("0")
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak if peak > 0 else Decimal("0"))
    return drawdown
