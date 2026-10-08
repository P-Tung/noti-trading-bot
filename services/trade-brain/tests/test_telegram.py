import asyncio
import unittest
from datetime import datetime, timezone
from decimal import Decimal

import httpx

from trade_brain.contracts import Profile, Side, StatisticsStatus, TradeCandidate
from trade_brain.paper import PaperRecommendation, PaperState
from trade_brain.telegram import TelegramCommandPoller, TelegramError, TelegramNotifier


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


async def _evaluation_result() -> str:
    return "PAPER evaluation result"


if __name__ == "__main__":
    unittest.main()
