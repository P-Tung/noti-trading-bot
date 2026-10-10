"""Shared, human-readable evaluation summaries for Telegram and dashboard."""

from collections.abc import Mapping, Sequence

from trade_brain.contracts import Decision
from trade_brain.orchestration import DecisionCycleResult
from trade_brain.paper import PaperRecommendation


REASON_LABELS = {
    "NO_ELIGIBLE_CANDIDATE": "Chưa có ứng viên đạt đủ điều kiện máy",
    "SNAPSHOT_DATA_INVALID": "Dữ liệu snapshot không hợp lệ",
    "RISK_INPUT_MISSING": "Thiếu dữ liệu rủi ro",
    "DATA_ERROR": "Lỗi hoặc thiếu dữ liệu bắt buộc",
    "OUT_OF_SCOPE": "Mã nằm ngoài phạm vi V2",
    "VOLUME_BELOW_MIN": "Volume 24h dưới ngưỡng V2",
    "SYMBOL_NOT_TRADING": "Mã không ở trạng thái TRADING",
    "UNIVERSE_STALE": "Dữ liệu universe đã quá cũ",
    "CANDIDATE_NOT_ELIGIBLE": "Ứng viên bị cổng rủi ro chặn",
}


def build_evaluation_summary(
    results: Sequence[DecisionCycleResult],
    recommendations: Sequence[PaperRecommendation],
    snapshot_symbols: Mapping[str, str] | None = None,
) -> list[dict[str, object]]:
    recommendation_profiles = {recommendation.profile.value for recommendation in recommendations}
    summary: list[dict[str, object]] = []
    for result in results:
        symbol = (snapshot_symbols or {}).get(result.snapshot_id, result.snapshot_id[:12])
        candidates = getattr(result, "candidates", ())
        candidates_by_id = {candidate.candidate_id: candidate for candidate in candidates}
        decisions: list[dict[str, object]] = []
        for decision in result.decisions:
            decision_value = getattr(decision.decision, "value", decision.decision)
            selected_id = getattr(decision, "selected_candidate_id", None) or getattr(decision, "watch_candidate_id", None)
            candidate = candidates_by_id.get(selected_id) if selected_id else None
            risk_results = getattr(result, "risk_results", {})
            risk = risk_results.get(selected_id) if selected_id else None
            has_full_audit = hasattr(result, "candidates")
            profile_value = getattr(decision.profile, "value", decision.profile)
            icon = _decision_icon(decision_value, profile_value in recommendation_profiles)
            if not has_full_audit and icon == "🔴":
                continue
            reasons = [*_translate_reasons(getattr(decision, "reason_codes", []))]
            if candidate is not None and not candidate.eligible:
                reasons.extend(_translate_reasons(candidate.eligibility_reasons))
            if risk is not None:
                reasons.extend(_translate_reasons(risk.codes))
            decisions.append(
                {
                    "profile": profile_value,
                    "decision": decision_value,
                    "icon": icon,
                    "summary_vi": getattr(decision, "summary_vi", "Không có mô tả từ backend"),
                    "reasons": _unique(reasons) or ["Không có lý do bổ sung từ backend"],
                    "candidate": candidate.model_dump(mode="json") if candidate else None,
                    "eligible_count": sum(candidate_item.eligible for candidate_item in candidates),
                    "candidate_count": len(candidates),
                }
            )
        summary.append(
            {
                "symbol": symbol,
                "snapshot_id": result.snapshot_id,
                "candidate_count": len(candidates),
                "eligible_count": sum(candidate.eligible for candidate in candidates),
                "decisions": decisions,
            }
        )
    return summary


def format_evaluation_summary(summary: Sequence[Mapping[str, object]]) -> str:
    """Format one queue result without silently dropping rejected symbols."""
    lines = [
        "📊 KẾT QUẢ ĐÁNH GIÁ PAPER",
        f"📦 {len(summary)} mã đã quét",
    ]
    for item in summary:
        lines.append(f"\n📈 {item.get('symbol', 'Không rõ mã')}")
        decisions = item.get("decisions", [])
        if not isinstance(decisions, list):
            continue
        for decision in decisions:
            if not isinstance(decision, dict):
                continue
            lines.append(
                f"{decision.get('icon', '🔴')} {_profile_label(str(decision.get('profile', '')))}: "
                f"{_decision_label(str(decision.get('decision', '')))}"
            )
            lines.append(f"  Vì sao: {decision.get('summary_vi', 'Không có mô tả')}")
            reasons = decision.get("reasons", [])
            if isinstance(reasons, list) and reasons:
                lines.append(f"  Chi tiết: {'; '.join(str(reason) for reason in reasons[:4])}")
            candidate = decision.get("candidate")
            if isinstance(candidate, dict):
                lines.append(
                    f"  Setup {candidate.get('strategy')} {candidate.get('entry_stage')} "
                    f"{candidate.get('side')} | Entry {candidate.get('entry_estimate')} "
                    f"| SL {candidate.get('stop_price')} | TP {candidate.get('target_price')}"
                )
    lines.append("\nChú thích: 🟢 Có thể trade PAPER | 🟡 Chờ điều kiện | 🔴 Không trade")
    return "\n".join(lines)


def _translate_reasons(reasons: Sequence[str]) -> list[str]:
    return [REASON_LABELS.get(reason, reason.replace("_", " ").lower()) for reason in reasons]


def _unique(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _decision_icon(decision: Decision | str, has_recommendation: bool) -> str:
    decision_value = getattr(decision, "value", decision)
    if decision_value in {Decision.LONG.value, Decision.SHORT.value} and has_recommendation:
        return "🟢"
    if decision_value == Decision.WAIT.value:
        return "🟡"
    return "🔴"


def _decision_label(decision: str) -> str:
    return {
        "LONG": "LONG",
        "SHORT": "SHORT",
        "WAIT": "CHỜ ĐIỀU KIỆN",
        "NO_TRADE": "KHÔNG TRADE",
    }.get(decision, decision)


def _profile_label(profile: str) -> str:
    return {"PROACTIVE": "Chủ động", "BALANCED": "Cân bằng", "CAUTIOUS": "Thận trọng"}.get(profile, profile)
