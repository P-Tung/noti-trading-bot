"""Telegram alert adapter for paper-trading state changes."""

import asyncio
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import TYPE_CHECKING

import httpx

from trade_brain.paper import PaperRecommendation, PaperState
from trade_brain.reporting import PaperReport

if TYPE_CHECKING:
    from trade_brain.orchestration import DecisionCycleResult


class TelegramError(RuntimeError):
    """Raised when Telegram rejects an alert."""


def configured_chat_ids() -> tuple[str, ...]:
    """Read one or more authorized chats without exposing their values."""
    raw_ids = os.environ.get("TELEGRAM_CHAT_IDS") or os.environ.get("TELEGRAM_CHAT_ID", "")
    return tuple(dict.fromkeys(chat_id.strip() for chat_id in raw_ids.split(",") if chat_id.strip()))


def _normalise_chat_ids(chat_ids: str | Sequence[str]) -> tuple[str, ...]:
    if isinstance(chat_ids, str):
        values = (chat_ids,)
    else:
        values = tuple(chat_ids)
    normalised = tuple(dict.fromkeys(str(chat_id).strip() for chat_id in values if str(chat_id).strip()))
    if not normalised:
        raise ValueError("at least one chat_id is required")
    return normalised


class TelegramNotifier:
    """Send concise alerts through the Telegram Bot API."""

    def __init__(self, bot_token: str, chat_ids: str | Sequence[str], client: httpx.AsyncClient | None = None) -> None:
        if not bot_token:
            raise ValueError("bot_token is required")
        self._bot_token = bot_token
        self._chat_ids = _normalise_chat_ids(chat_ids)
        self._client = client or httpx.AsyncClient(base_url="https://api.telegram.org", timeout=10.0)
        self._owns_client = client is None

    @classmethod
    def from_environment(cls) -> "TelegramNotifier":
        """Create a notifier from server-only environment variables."""
        return cls(
            os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            configured_chat_ids(),
        )

    async def close(self) -> None:
        """Close the owned HTTP client."""
        if self._owns_client:
            await self._client.aclose()

    async def send(self, message: str) -> None:
        """Send one message and fail explicitly when Telegram rejects it."""
        if not message.strip():
            raise ValueError("Telegram message cannot be empty")
        for chat_id in self._chat_ids:
            for attempt in range(2):
                try:
                    response = await self._client.post(
                        f"/bot{self._bot_token}/sendMessage",
                        json={"chat_id": chat_id, "text": message[:4096]},
                    )
                    if response.status_code == 429 or response.status_code >= 500:
                        if attempt == 0:
                            await asyncio.sleep(0.2)
                            continue
                    response.raise_for_status()
                    body = response.json()
                    if not isinstance(body, dict) or body.get("ok") is not True:
                        raise TelegramError("Telegram rejected the message")
                    break
                except httpx.RequestError:
                    if attempt == 0:
                        await asyncio.sleep(0.2)
                        continue
                    raise


class TelegramCommandPoller:
    """Poll authorized Telegram commands and dispatch on-demand evaluations."""

    def __init__(
        self,
        bot_token: str,
        chat_ids: str | Sequence[str],
        on_evaluate: Callable[[], Awaitable[str]],
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not bot_token:
            raise ValueError("bot_token is required")
        self._bot_token = bot_token
        self._chat_ids = set(_normalise_chat_ids(chat_ids))
        self._on_evaluate = on_evaluate
        self._client = client or httpx.AsyncClient(
            base_url="https://api.telegram.org",
            timeout=httpx.Timeout(35.0, connect=10.0),
        )
        self._owns_client = client is None

    @classmethod
    def from_environment(cls, on_evaluate: Callable[[], Awaitable[str]]) -> "TelegramCommandPoller":
        """Create a command poller from server-only environment variables."""
        return cls(
            os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            configured_chat_ids(),
            on_evaluate,
        )

    async def close(self) -> None:
        """Close the polling client."""
        if self._owns_client:
            await self._client.aclose()

    async def run_forever(self) -> None:
        """Long-poll Telegram until the worker is cancelled."""
        offset: int | None = None
        while True:
            try:
                updates = await self._get_updates(offset)
            except (httpx.ReadTimeout, httpx.RequestError):
                await asyncio.sleep(1)
                continue
            for update in updates:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    offset = update_id + 1
                await self._handle_update(update)

    async def _get_updates(self, offset: int | None) -> list[dict[str, object]]:
        params: dict[str, object] = {
            "timeout": 25,
            "allowed_updates": '["message"]',
        }
        if offset is not None:
            params["offset"] = offset
        response = await self._client.get(f"/bot{self._bot_token}/getUpdates", params=params)
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or body.get("ok") is not True:
            raise TelegramError("Telegram rejected getUpdates")
        updates = body.get("result", [])
        if not isinstance(updates, list):
            raise TelegramError("Telegram returned invalid updates")
        return [update for update in updates if isinstance(update, dict)]

    async def _handle_update(self, update: dict[str, object]) -> None:
        message = update.get("message")
        if not isinstance(message, dict):
            return
        chat = message.get("chat")
        text = message.get("text")
        if not isinstance(chat, dict) or not isinstance(text, str):
            return
        if str(chat.get("id")) not in self._chat_ids:
            return
        command = text.strip().split(maxsplit=1)[0].split("@", maxsplit=1)[0].lower()
        if command == "/start" or command == "/help":
            await self._send("Lệnh khả dụng: /danh-gia để chạy đánh giá PAPER ngay.", str(chat.get("id")))
            return
        if command not in {"/danh-gia", "/danhgia", "/danh_gia", "/evaluate", "/evaluate_now"}:
            return
        try:
            result = await self._on_evaluate()
        except Exception:
            result = "Không thể hoàn tất đánh giá PAPER lúc này. Xem log Trade Brain để biết chi tiết."
        await self._send(result, str(chat.get("id")))

    async def _send(self, message: str, chat_id: str) -> None:
        response = await self._client.post(
            f"/bot{self._bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": message[:4096]},
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or body.get("ok") is not True:
            raise TelegramError("Telegram rejected the command response")


def format_recommendation(recommendation: PaperRecommendation) -> str:
    """Format a state change without exposing credentials or raw payloads."""
    candidate = recommendation.candidate
    return (
        f"PAPER {recommendation.profile.value}\n"
        f"Setup {candidate.setup_id} {candidate.side.value}\n"
        f"Entry {candidate.entry_estimate}\n"
        f"SL {candidate.stop_price} | TP {candidate.target_price}\n"
        f"State: {recommendation.state.value}"
    )


def format_report(report: PaperReport) -> str:
    """Format a cohort report for Telegram."""
    profile = report.profile.value if report.profile else "ALL"
    rate = f"{report.net_positive_rate:.2%}" if report.net_positive_rate is not None else "N/A"
    return (
        f"PAPER REPORT {profile} #{report.milestone}\n"
        f"Recommendations: {report.recommendation_count}\n"
        f"Closed: {report.closed_count} | Open: {report.open_count}\n"
        f"Win rate: {rate}\n"
        f"Net P&L: {report.total_net_pnl} USDT\n"
        f"Net R: {report.total_net_r}\n"
        f"Status: {report.cohort_status}"
    )


def format_evaluation(
    results: Sequence["DecisionCycleResult"],
    recommendations: Sequence[PaperRecommendation],
    snapshot_symbols: Mapping[str, str] | None = None,
) -> str:
    """Format every on-demand evaluation with compact visual status markers."""
    recommendation_profiles = {recommendation.profile for recommendation in recommendations}
    lines = [
        "📊 ĐÁNH GIÁ PAPER THEO YÊU CẦU",
        f"📦 {len(results)} mã | {sum(len(result.decisions) for result in results)} quyết định",
    ]
    for result in results:
        symbol = (snapshot_symbols or {}).get(result.snapshot_id, result.snapshot_id[:12])
        lines.append(f"\n📈 {symbol}")
        for decision in result.decisions:
            icon = _decision_icon(decision.decision.value, decision.profile in recommendation_profiles)
            label = _profile_label(decision.profile.value)
            lines.append(f"{icon} {label}: {_decision_label(decision.decision.value)}")
    if recommendations:
        lines.append("\n🟢 PAPER TRADE ĐỀ XUẤT")
        for recommendation in recommendations:
            candidate = recommendation.candidate
            lines.extend(
                [
                    f"• {_profile_label(recommendation.profile.value)}: {candidate.side.value}",
                    f"  Entry {candidate.entry_estimate} | SL {candidate.stop_price} | TP {candidate.target_price}",
                    f"  Trạng thái: {recommendation.state.value}",
                ]
            )
    else:
        lines.append("\n📭 Không có đề xuất trade PAPER trong chu kỳ này.")
    lines.append("\nChú thích: 🟢 Có thể trade PAPER | 🟡 Chờ điều kiện | 🔴 Không trade")
    return "\n".join(lines)


def _decision_icon(decision: str, has_recommendation: bool) -> str:
    if decision in {"LONG", "SHORT"} and has_recommendation:
        return "🟢"
    if decision == "WAIT":
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
    return {
        "PROACTIVE": "Chủ động",
        "BALANCED": "Cân bằng",
        "CAUTIOUS": "Thận trọng",
    }.get(profile, profile)
