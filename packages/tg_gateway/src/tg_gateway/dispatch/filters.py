"""Composable, synchronous update filters: ``command("start") & private_chat()``, ``~text()``."""

from __future__ import annotations

from collections.abc import Callable

from tg_gateway.dispatch.context import Context

__all__ = ["Filter", "callback_prefix", "command", "private_chat", "text", "update_type"]


class Filter:
    def __init__(self, check: Callable[[Context], bool], name: str) -> None:
        self._check = check
        self._name = name

    def __call__(self, ctx: Context) -> bool:
        return self._check(ctx)

    def __and__(self, other: Filter) -> Filter:
        return Filter(lambda c: self(c) and other(c), f"({self._name} & {other._name})")

    def __or__(self, other: Filter) -> Filter:
        return Filter(lambda c: self(c) or other(c), f"({self._name} | {other._name})")

    def __invert__(self) -> Filter:
        return Filter(lambda c: not self(c), f"~{self._name}")

    def __repr__(self) -> str:
        return f"Filter({self._name})"


def command(*names: str, bot_username: str | None = None) -> Filter:
    """Matches ``/name``, ``/name@bot`` and ``/name args``; sets data["command_args"].

    With `bot_username`, ``/name@other_bot`` (meant for another bot in a group) doesn't match.
    """
    wanted = {name.lower().lstrip("/") for name in names}
    own = bot_username.lower().lstrip("@") if bot_username else None

    def check(ctx: Context) -> bool:
        body = ctx.text
        if not body or not body.startswith("/"):
            return False
        head, *rest = body[1:].split(maxsplit=1)
        name, _, mention = head.partition("@")
        if name.lower() not in wanted or (mention and own and mention.lower() != own):
            return False
        ctx.data["command"] = name.lower()
        ctx.data["command_args"] = rest[0].strip() if rest else ""
        return True

    return Filter(check, f"command({', '.join(sorted(wanted))})")


def text() -> Filter:
    """A text message that isn't a command."""
    return Filter(lambda c: c.text is not None and not c.text.startswith("/"), "text")


def callback_prefix(prefix: str) -> Filter:
    return Filter(
        lambda c: c.callback_data is not None and c.callback_data.startswith(prefix),
        f"callback_prefix({prefix!r})",
    )


def private_chat() -> Filter:
    def check(ctx: Context) -> bool:
        message = ctx.message or (ctx.callback_query.message if ctx.callback_query else None)
        return message is not None and message.chat.type == "private"

    return Filter(check, "private_chat")


def update_type(name: str) -> Filter:
    """e.g. "message", "callback_query", "my_chat_member"."""
    return Filter(lambda c: getattr(c.update, name, None) is not None, f"update_type({name})")
