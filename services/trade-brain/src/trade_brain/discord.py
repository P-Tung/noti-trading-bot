"""Discord adapter for paper-trading alerts and on-demand commands."""

import logging
import os
from collections.abc import Awaitable, Callable, Sequence

import httpx


LOGGER = logging.getLogger(__name__)


class DiscordError(RuntimeError):
    """Raised when Discord rejects a bot request."""


class DiscordNotifier:
    """Send messages to a configured Discord text channel."""

    def __init__(self, bot_token: str, channel_id: str, client: httpx.AsyncClient | None = None) -> None:
        if not bot_token or not channel_id:
            raise ValueError("bot_token and channel_id are required")
        self._bot_token = bot_token
        self._channel_id = str(channel_id)
        self._client = client or httpx.AsyncClient(base_url="https://discord.com/api/v10", timeout=10.0)
        self._owns_client = client is None

    @classmethod
    def from_environment(cls) -> "DiscordNotifier":
        return cls(
            os.environ.get("DISCORD_BOT_TOKEN", ""),
            os.environ.get("DISCORD_CHANNEL_ID", ""),
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def send(self, message: str) -> None:
        if not message.strip():
            raise ValueError("Discord message cannot be empty")
        response = await self._client.post(
            f"/channels/{self._channel_id}/messages",
            headers={"Authorization": f"Bot {self._bot_token}"},
            json={"content": message[:2000]},
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or not body.get("id"):
            raise DiscordError("Discord rejected the message")


class DiscordCommandPoller:
    """Receive text commands through the Discord Gateway."""

    COMMANDS = {"/danh-gia", "/danhgia", "/danh_gia", "/evaluate", "/evaluate_now"}

    def __init__(self, bot_token: str, on_evaluate: Callable[[], Awaitable[str]]) -> None:
        if not bot_token:
            raise ValueError("bot_token is required")
        self._bot_token = bot_token
        self._on_evaluate = on_evaluate
        self._client = None
        self._guild_id = os.environ.get("DISCORD_GUILD_ID", "")

    @classmethod
    def from_environment(cls, on_evaluate: Callable[[], Awaitable[str]]) -> "DiscordCommandPoller":
        return cls(os.environ.get("DISCORD_BOT_TOKEN", ""), on_evaluate)

    async def run_forever(self) -> None:
        try:
            import discord
            from discord.ext import commands
        except ImportError as error:
            raise DiscordError("Install discord.py to enable Discord commands") from error

        intents = discord.Intents.default()
        intents.message_content = True
        client = commands.Bot(command_prefix="!", intents=intents)
        self._client = client

        async def evaluate_result() -> str:
            try:
                return await self._on_evaluate()
            except Exception:
                return "Không thể hoàn tất đánh giá PAPER lúc này. Xem log Trade Brain để biết chi tiết."

        async def slash_evaluate(interaction: discord.Interaction) -> None:
            await interaction.response.defer()
            await interaction.followup.send((await evaluate_result())[:2000])

        guild = discord.Object(id=int(self._guild_id)) if self._guild_id else None
        command = discord.app_commands.Command(
            name="danh-gia",
            description="Chạy đánh giá PAPER theo yêu cầu",
            callback=slash_evaluate,
        )
        client.tree.add_command(command, guild=guild)

        @client.event
        async def on_ready() -> None:
            if guild is not None:
                await client.tree.sync(guild=guild)
            else:
                await client.tree.sync()

        @client.event
        async def on_message(message: object) -> None:
            if getattr(getattr(message, "author", None), "bot", False):
                return
            content = getattr(message, "content", "")
            command = content.strip().split(maxsplit=1)[0].lower() if content.strip() else ""
            if command in {"/start", "/help"}:
                await message.channel.send("Lệnh khả dụng: /danh-gia để chạy đánh giá PAPER ngay.")
                return
            if command not in self.COMMANDS:
                return
            try:
                result = await evaluate_result()
            except Exception:
                result = "Không thể hoàn tất đánh giá PAPER lúc này. Xem log Trade Brain để biết chi tiết."
            await message.channel.send(result[:2000])

        await client.start(self._bot_token)

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed():
            await self._client.close()


class BroadcastNotifier:
    """Fan out alerts to configured notification channels."""

    def __init__(self, sinks: Sequence[object]) -> None:
        self._sinks = tuple(sinks)

    async def send(self, message: str) -> None:
        for sink in self._sinks:
            try:
                await sink.send(message)
            except (DiscordError, httpx.HTTPError, RuntimeError) as error:
                LOGGER.warning("Notification delivery failed: %s", error)

    async def close(self) -> None:
        for sink in self._sinks:
            close = getattr(sink, "close", None)
            if close is not None:
                await close()
