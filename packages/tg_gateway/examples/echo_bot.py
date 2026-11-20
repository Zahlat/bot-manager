"""Minimal echo bot: proves tg_gateway end to end in polling mode (the Phase 1 "Done" gate).

Setup: put your *dev* bot token in `.env` as BOT_TOKEN=... (see .env.example), then:

    uv run python packages/tg_gateway/examples/echo_bot.py

/start shows a greeting with a signed "Ping" button; any text is echoed back (HTML-escaped);
Ctrl+C stops cleanly and confirms the last handled update to Telegram.
"""

from __future__ import annotations

import asyncio
import html
import logging
import sys

import httpx
from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from tg_gateway import (
    BotClient,
    CallbackCodec,
    Context,
    Dispatcher,
    InlineKeyboardBuilder,
    InMemorySendLimiter,
    InvalidCallbackData,
    PollingSource,
    Router,
    callback_prefix,
    command,
    text,
)
from tg_gateway.types import Update

log = logging.getLogger("echo_bot")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: SecretStr
    # Dev default only. Real bots load a 32+ byte random CALLBACK_HMAC_KEY from the environment.
    callback_hmac_key: SecretStr = SecretStr("echo-bot-dev-only-callback-key-0000")


def build_router(codec: CallbackCodec) -> Router:
    router = Router("echo")

    @router.handler(command("start"))
    async def start(ctx: Context) -> None:
        if ctx.chat_id is None:
            return
        keyboard = InlineKeyboardBuilder().button("Ping", callback_data=codec.encode("ping"))
        await ctx.bot.send_message(
            ctx.chat_id,
            "<b>مرحبا!</b> Send me any text and I will echo it back.",
            reply_markup=keyboard.build(),
        )

    @router.handler(text())
    async def echo(ctx: Context) -> None:
        if ctx.chat_id is not None and ctx.text is not None:
            await ctx.bot.send_message(ctx.chat_id, html.escape(ctx.text))

    @router.handler(callback_prefix("ping"))
    async def ping(ctx: Context) -> None:
        query = ctx.callback_query
        if query is None or query.data is None:
            return
        try:
            codec.decode(query.data)
        except InvalidCallbackData:
            await ctx.bot.answer_callback_query(query.id, text="Invalid button")
            return
        await ctx.bot.answer_callback_query(query.id, text="pong")

    return router


async def run_bot(bot: BotClient, codec: CallbackCodec, *, stop: asyncio.Event) -> None:
    dispatcher = Dispatcher(build_router(codec), bot)

    async def handle(update: Update) -> None:
        await dispatcher.feed(update)

    me = await bot.get_me()
    log.info("running as @%s (polling); press Ctrl+C to stop", me.username)
    await PollingSource(bot).run(handle, stop=stop)


async def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    try:
        settings = Settings()  # type: ignore[call-arg]  # values come from the environment / .env
    except ValidationError:
        # No traceback on purpose: settings errors can echo input values (secrets).
        log.error("BOT_TOKEN is missing: add it to .env (see .env.example)")  # noqa: TRY400
        return 2
    codec = CallbackCodec(settings.callback_hmac_key)
    async with httpx.AsyncClient() as http:
        bot = BotClient(settings.bot_token, http=http, limiter=InMemorySendLimiter())
        await run_bot(bot, codec, stop=asyncio.Event())
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        # asyncio.run already cancelled the bot; PollingSource confirmed the last offset.
        log.info("stopped")
