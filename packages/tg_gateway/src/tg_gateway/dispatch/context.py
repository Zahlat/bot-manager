"""Per-update context handed to filters, middleware and handlers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from tg_gateway.client import BotClient
from tg_gateway.types import CallbackQuery, Message, Update

__all__ = ["Context", "ErrorHandler", "Handler", "Middleware"]


@dataclass(slots=True)
class Context:
    update: Update
    bot: BotClient
    # Scratch space: middleware stores things here (user, language, ...) for handlers to read.
    data: dict[str, object] = field(default_factory=dict)

    @property
    def message(self) -> Message | None:
        return self.update.message or self.update.edited_message

    @property
    def callback_query(self) -> CallbackQuery | None:
        return self.update.callback_query

    @property
    def text(self) -> str | None:
        return self.message.text if self.message else None

    @property
    def callback_data(self) -> str | None:
        return self.callback_query.data if self.callback_query else None

    @property
    def chat_id(self) -> int | None:
        if self.message:
            return self.message.chat.id
        if self.callback_query and self.callback_query.message:
            return self.callback_query.message.chat.id
        if self.update.my_chat_member:
            return self.update.my_chat_member.chat.id
        return None

    @property
    def user_id(self) -> int | None:
        if self.message:
            return self.message.from_.id if self.message.from_ else None
        if self.callback_query:
            return self.callback_query.from_.id
        if self.update.my_chat_member:
            return self.update.my_chat_member.from_.id
        return None


Handler = Callable[[Context], Awaitable[None]]
Middleware = Callable[[Context, Handler], Awaitable[None]]
ErrorHandler = Callable[[Context, Exception], Awaitable[None]]
