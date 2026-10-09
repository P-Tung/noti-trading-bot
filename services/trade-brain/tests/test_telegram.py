import asyncio
import unittest
from datetime import datetime, timezone
from decimal import Decimal

import httpx
from types import SimpleNamespace

from trade_brain.contracts import Profile, Side, StatisticsStatus, TradeCandidate
from trade_brain.paper import PaperRecommendation, PaperState
from trade_brain.telegram import (
    TelegramCommandPoller,
    TelegramError,
    TelegramNotifier,
    format_evaluation,
    format_immediate_recommendations,
)


class TelegramTests(unittest.TestCase):
    def test_sends_message_to_bot_api(self) -> None:
        requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"ok": True, "result": {}})

        async def run() -> None:
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://test")
            notifier = TelegramNotifier("token", "chat", client)
            await notifier.send("hello")

        asyncio.run(run())
        self.assertEqual(requests[0].url.path, "/bottoken/sendMessage")
        self.assertEqual(requests[0].content, b'{"chat_id":"chat","text":"hello"}')

    def test_rejects_failed_telegram_response(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": False})

        async def run() -> None:
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://test")
            notifier = TelegramNotifier("token", "chat", client)
            with self.assertRaises(TelegramError):
                await notifier.send("hello")

        asyncio.run(run())

    def test_retries_transient_server_failure_once(self) -> None:
        attempts = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(503)
            return httpx.Response(200, json={"ok": True, "result": {}})

        async def run() -> None:
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://test")
            notifier = TelegramNotifier("token", "chat", client)
            await notifier.send("hello")
            await client.aclose()

        asyncio.run(run())
        self.assertEqual(attempts, 2)

    def test_does_not_retry_permanent_client_error(self) -> None:
        attempts = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(400, json={"ok": False})

        async def run() -> None:
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://test")
            notifier = TelegramNotifier("token", "chat", client)
            with self.assertRaises(httpx.HTTPStatusError):
                await notifier.send("hello")
            await client.aclose()

        asyncio.run(run())
        self.assertEqual(attempts, 1)

    def test_command_poller_dispatches_evaluate_for_authorized_chat(self) -> None:
        requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"ok": True, "result": {}})

        async def run() -> None:
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://test")
            poller = TelegramCommandPoller("token", "chat", lambda: _evaluation_result(), client)
            await poller._handle_update(
                {"message": {"chat": {"id": "chat"}, "text": "/danh-gia"}}
            )

        asyncio.run(run())
        self.assertEqual(requests[0].url.path, "/bottoken/sendMessage")
        self.assertIn(b"PAPER evaluation", requests[0].content)

    def test_start_registers_new_chat_and_replies(self) -> None:
        requests: list[httpx.Request] = []
        registered: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"ok": True, "result": {}})

        async def register(chat_id: str) -> None:
            registered.append(chat_id)

        async def run() -> None:
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://test")
            poller = TelegramCommandPoller(
                "token",
                (),
                lambda: _evaluation_result(),
                client,
                register,
            )
            await poller._handle_update({"message": {"chat": {"id": "new-chat"}, "text": "/start"}})

        asyncio.run(run())
        self.assertEqual(registered, ["new-chat"])
        self.assertIn("đăng ký nhận tin", requests[0].content.decode())

    def test_notifier_broadcasts_to_registered_chats(self) -> None:
        requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"ok": True, "result": {}})

        async def run() -> None:
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://test")
            notifier = TelegramNotifier("token", (), client)
            notifier.add_chat_id("one")
            notifier.add_chat_id("two")
            await notifier.send("hello")

        asyncio.run(run())
        self.assertEqual(len(requests), 2)

    def test_evaluate_result_can_be_broadcast_instead_of_replying_once(self) -> None:
        requests: list[httpx.Request] = []
        broadcasts: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"ok": True, "result": {}})

        async def broadcast(message: str) -> None:
            broadcasts.append(message)

        async def run() -> None:
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://test")
            poller = TelegramCommandPoller(
                "token",
                ("one", "two"),
                lambda: _evaluation_result(),
                client,
                on_evaluate_result=broadcast,
            )
            await poller._handle_update(
                {"message": {"chat": {"id": "one"}, "text": "/danh-gia"}}
            )

        asyncio.run(run())
        self.assertEqual(broadcasts, ["PAPER evaluation result"])
        self.assertEqual(requests, [])

    def test_evaluation_hides_no_trade_symbols(self) -> None:
        result = SimpleNamespace(
            snapshot_id="snapshot-1",
            decisions=(
                SimpleNamespace(decision=SimpleNamespace(value="NO_TRADE"), profile=Profile.PROACTIVE),
                SimpleNamespace(decision=SimpleNamespace(value="WAIT"), profile=Profile.BALANCED),
            ),
        )

        message = format_evaluation([result], [], {"snapshot-1": "BTCUSDT"})

        self.assertIn("BTCUSDT", message)
        self.assertIn("🟡 Cân bằng: CHỜ ĐIỀU KIỆN", message)
        self.assertNotIn("Chủ động: KHÔNG TRADE", message)
        self.assertIn("🔴 Không trade", message)

    def test_immediate_recommendation_includes_symbol_and_trade_details(self) -> None:
        recommendation = SimpleNamespace(
            profile=Profile.BALANCED,
            candidate=SimpleNamespace(
                side=SimpleNamespace(value="LONG"),
                entry_estimate=100,
                stop_price=95,
                target_price=110,
            ),
            state=SimpleNamespace(value="OPEN"),
        )

        message = format_immediate_recommendations("BTCUSDT", [recommendation])

        self.assertIn("🟢 CÓ THỂ TRADE PAPER", message)
        self.assertIn("BTCUSDT", message)
        self.assertIn("Entry 100 | SL 95 | TP 110", message)


async def _evaluation_result() -> str:
    return "PAPER evaluation result"


if __name__ == "__main__":
    unittest.main()
