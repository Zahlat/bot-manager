"""Slice 5: router, filters, middleware (Chain of Responsibility) and error isolation."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

import httpx
import pytest
from pydantic import SecretStr

from tg_gateway.client import BotClient
from tg_gateway.dispatch import (
    Context,
    Dispatcher,
    Handler,
    Router,
    callback_prefix,
    command,
    private_chat,
    text,
    update_type,
)
from tg_gateway.types import Update

pytestmark = pytest.mark.unit

USER = {"id": 7, "is_bot": False, "first_name": "Sami"}


def msg(body: str, *, chat_type: str = "private", update_id: int = 1) -> Update:
    chat = {"id": 100, "type": chat_type}
    return Update.model_validate(
        {
            "update_id": update_id,
            "message": {"message_id": 1, "date": 0, "chat": chat, "from": USER, "text": body},
        }
    )


def cbq(data: str) -> Update:
    message = {"message_id": 5, "date": 0, "chat": {"id": 100, "type": "private"}}
    return Update.model_validate(
        {
            "update_id": 2,
            "callback_query": {
                "id": "q",
                "from": USER,
                "chat_instance": "ci",
                "message": message,
                "data": data,
            },
        }
    )


def member_update() -> Update:
    member = {"user": {"id": 1, "is_bot": True, "first_name": "Bot"}, "status": "kicked"}
    return Update.model_validate(
        {
            "update_id": 3,
            "my_chat_member": {
                "chat": {"id": 100, "type": "private"},
                "from": USER,
                "date": 0,
                "old_chat_member": {**member, "status": "member"},
                "new_chat_member": member,
            },
        }
    )


@pytest.fixture
async def bot() -> AsyncIterator[BotClient]:
    async with httpx.AsyncClient() as http:  # never used: dispatch tests make no API calls
        yield BotClient(SecretStr("1:x"), http=http)


def ctx(bot: BotClient, update: Update) -> Context:
    return Context(update=update, bot=bot)


# --------------------------------------------------------------------------- context


def test_context_shortcuts(bot: BotClient) -> None:
    message_ctx = ctx(bot, msg("hi"))
    callback_ctx = ctx(bot, cbq("x"))
    member_ctx = ctx(bot, member_update())

    assert (message_ctx.chat_id, message_ctx.text, message_ctx.user_id) == (100, "hi", 7)
    assert (callback_ctx.chat_id, callback_ctx.callback_data, callback_ctx.user_id) == (100, "x", 7)
    assert callback_ctx.text is None
    assert (member_ctx.chat_id, member_ctx.user_id) == (100, 7)


# --------------------------------------------------------------------------- filters


@pytest.mark.parametrize(
    ("body", "matches", "args"),
    [
        ("/start", True, ""),
        ("/start res_123", True, "res_123"),
        ("/start   two  words ", True, "two  words"),
        ("/START", True, ""),
        ("/start@my_bot", True, ""),
        ("/start@My_Bot payload", True, "payload"),
        ("/start@other_bot", False, None),
        ("/startx", False, None),
        ("start", False, None),
        ("hello /start", False, None),
    ],
)
def test_command_parsing(bot: BotClient, body: str, matches: bool, args: str | None) -> None:
    c = ctx(bot, msg(body))

    assert command("start", bot_username="my_bot")(c) is matches
    assert c.data.get("command_args") == args


def test_command_without_bot_username_accepts_any_mention(bot: BotClient) -> None:
    assert command("start")(ctx(bot, msg("/start@whatever_bot")))


def test_command_accepts_several_names(bot: BotClient) -> None:
    f = command("help", "h")

    assert f(ctx(bot, msg("/h")))
    assert f(ctx(bot, msg("/help")))
    assert not f(ctx(bot, msg("/start")))


def test_command_ignores_callbacks(bot: BotClient) -> None:
    assert not command("start")(ctx(bot, cbq("/start")))


def test_text_matches_plain_text_only(bot: BotClient) -> None:
    assert text()(ctx(bot, msg("hello")))
    assert not text()(ctx(bot, msg("/start")))
    assert not text()(ctx(bot, cbq("hello")))


def test_callback_prefix(bot: BotClient) -> None:
    assert callback_prefix("cat:")(ctx(bot, cbq("cat:12")))
    assert not callback_prefix("cat:")(ctx(bot, cbq("res:12")))
    assert not callback_prefix("cat:")(ctx(bot, msg("cat:12")))


def test_private_chat(bot: BotClient) -> None:
    assert private_chat()(ctx(bot, msg("x")))
    assert not private_chat()(ctx(bot, msg("x", chat_type="supergroup")))


def test_update_type(bot: BotClient) -> None:
    assert update_type("my_chat_member")(ctx(bot, member_update()))
    assert not update_type("message")(ctx(bot, member_update()))


def test_filter_combinators(bot: BotClient) -> None:
    c = ctx(bot, msg("hello"))
    yes, no = text(), command("start")

    assert (yes & ~no)(c)
    assert not (yes & no)(c)
    assert (no | yes)(c)
    assert not (no | ~yes)(c)


# --------------------------------------------------------------------------- routing


async def test_first_registered_match_wins(bot: BotClient) -> None:
    router, calls = Router(), []

    @router.handler(text())
    async def first(c: Context) -> None:
        calls.append("first")

    @router.handler(text())
    async def second(c: Context) -> None:
        calls.append("second")

    assert await Dispatcher(router, bot).feed(msg("hi")) is True
    assert calls == ["first"]


async def test_nested_routers_are_searched_depth_first_in_order(bot: BotClient) -> None:
    root, child, calls = Router(), Router(), []

    @child.handler(text())
    async def in_child(c: Context) -> None:
        calls.append("child")

    root.include_router(child)

    @root.handler(text())
    async def in_root(c: Context) -> None:
        calls.append("root")

    @root.handler(command("start"))
    async def start(c: Context) -> None:
        calls.append("start")

    dispatcher = Dispatcher(root, bot)
    await dispatcher.feed(msg("hi"))
    await dispatcher.feed(msg("/start"))

    assert calls == ["child", "start"]


async def test_handler_without_filters_matches_everything(bot: BotClient) -> None:
    router, calls = Router(), []

    @router.handler()
    async def catch_all(c: Context) -> None:
        calls.append(c.update.update_id)

    await Dispatcher(router, bot).feed(member_update())

    assert calls == [3]


async def test_unmatched_update_returns_false(bot: BotClient) -> None:
    router = Router()

    @router.handler(command("start"))
    async def start(c: Context) -> None:
        raise AssertionError("must not run")

    assert await Dispatcher(router, bot).feed(msg("hello")) is False


# --------------------------------------------------------------------------- middleware


async def test_outer_middleware_wraps_routing_in_order_even_if_unmatched(bot: BotClient) -> None:
    dispatcher, events = Dispatcher(Router(), bot), []

    def make(name: str):  # type: ignore[no-untyped-def]  # tiny local factory
        async def mw(c: Context, call_next: Handler) -> None:
            events.append(f"{name}>")
            await call_next(c)
            events.append(f"<{name}")

        return mw

    dispatcher.middleware(make("a"))
    dispatcher.middleware(make("b"))

    assert await dispatcher.feed(msg("nobody handles this")) is False
    assert events == ["a>", "b>", "<b", "<a"]


async def test_outer_middleware_can_short_circuit(bot: BotClient) -> None:
    router, calls = Router(), []

    @router.handler()
    async def handler(c: Context) -> None:
        calls.append("handler")

    async def ban_check(c: Context, call_next: Handler) -> None:
        return  # banned: never calls next

    dispatcher = Dispatcher(router, bot)
    dispatcher.middleware(ban_check)

    assert await dispatcher.feed(msg("hi")) is False
    assert calls == []


async def test_middleware_passes_data_to_handler(bot: BotClient) -> None:
    router, seen = Router(), []

    @router.handler()
    async def handler(c: Context) -> None:
        seen.append(c.data["lang"])

    async def language(c: Context, call_next: Handler) -> None:
        c.data["lang"] = "ar"
        await call_next(c)

    dispatcher = Dispatcher(router, bot)
    dispatcher.middleware(language)
    await dispatcher.feed(msg("hi"))

    assert seen == ["ar"]


async def test_router_middleware_runs_only_around_matched_handlers_outer_first(
    bot: BotClient,
) -> None:
    root, child, events = Router(), Router(), []

    async def root_mw(c: Context, call_next: Handler) -> None:
        events.append("root")
        await call_next(c)

    async def child_mw(c: Context, call_next: Handler) -> None:
        events.append("child")
        await call_next(c)

    @child.handler(command("start"))
    async def start(c: Context) -> None:
        events.append("start")

    root.middleware(root_mw)
    child.middleware(child_mw)
    root.include_router(child)
    dispatcher = Dispatcher(root, bot)

    await dispatcher.feed(msg("unmatched"))
    assert events == []

    await dispatcher.feed(msg("/start"))
    assert events == ["root", "child", "start"]


# --------------------------------------------------------------------------- errors


async def test_handler_error_goes_to_nearest_on_error(bot: BotClient) -> None:
    root, child, seen = Router(), Router(), []

    @child.handler()
    async def boom(c: Context) -> None:
        raise ValueError("bad")

    async def child_errors(c: Context, exc: Exception) -> None:
        seen.append(("child", type(exc).__name__))

    async def root_errors(c: Context, exc: Exception) -> None:
        seen.append(("root", type(exc).__name__))

    child.on_error(child_errors)
    root.on_error(root_errors)
    root.include_router(child)

    assert await Dispatcher(root, bot).feed(msg("hi")) is True
    assert seen == [("child", "ValueError")]


async def test_handler_error_falls_back_to_parent_on_error(bot: BotClient) -> None:
    root, child, seen = Router(), Router(), []

    @child.handler()
    async def boom(c: Context) -> None:
        raise ValueError("bad")

    async def root_errors(c: Context, exc: Exception) -> None:
        seen.append("root")

    root.on_error(root_errors)
    root.include_router(child)
    await Dispatcher(root, bot).feed(msg("hi"))

    assert seen == ["root"]


async def test_unhandled_error_is_logged_without_payload_and_never_raises(
    bot: BotClient, caplog: pytest.LogCaptureFixture
) -> None:
    router = Router()

    @router.handler()
    async def boom(c: Context) -> None:
        raise ValueError("bad")

    with caplog.at_level(logging.ERROR):
        assert await Dispatcher(router, bot).feed(msg("my secret text", update_id=4242)) is True

    assert "4242" in caplog.text
    assert "my secret text" not in caplog.text


async def test_failing_error_handler_is_logged_not_raised(
    bot: BotClient, caplog: pytest.LogCaptureFixture
) -> None:
    router = Router()

    @router.handler()
    async def boom(c: Context) -> None:
        raise ValueError("bad")

    async def broken(c: Context, exc: Exception) -> None:
        raise RuntimeError("error handler bug")

    router.on_error(broken)
    with caplog.at_level(logging.ERROR):
        await Dispatcher(router, bot).feed(msg("hi"))

    assert "RuntimeError" in caplog.text


async def test_outer_middleware_error_is_contained(
    bot: BotClient, caplog: pytest.LogCaptureFixture
) -> None:
    async def broken(c: Context, call_next: Handler) -> None:
        raise RuntimeError("middleware bug")

    dispatcher = Dispatcher(Router(), bot)
    dispatcher.middleware(broken)
    with caplog.at_level(logging.ERROR):
        assert await dispatcher.feed(msg("hi")) is False

    assert "RuntimeError" in caplog.text


async def test_cancellation_is_not_swallowed(bot: BotClient) -> None:
    router = Router()

    @router.handler()
    async def cancelled(c: Context) -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await Dispatcher(router, bot).feed(msg("hi"))


def test_router_cannot_be_included_twice() -> None:
    root, other, child = Router(), Router(), Router()
    root.include_router(child)

    with pytest.raises(ValueError, match="already included"):
        other.include_router(child)
    with pytest.raises(ValueError, match="already included"):
        root.include_router(root)
