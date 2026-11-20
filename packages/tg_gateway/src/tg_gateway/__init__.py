"""Typed async Telegram Bot API gateway. May import only platform_kit.

The webhook (FastAPI) is imported explicitly from ``tg_gateway.transport.webhook``, so importing
this package never pulls in a web framework.
"""

from tg_gateway.callback import (
    CallbackCodec,
    CallbackDataError,
    CallbackDataTooLong,
    InvalidCallbackData,
)
from tg_gateway.client import BotClient, RetryAfterHook, Timeouts
from tg_gateway.dispatch import (
    Context,
    Dispatcher,
    Filter,
    Handler,
    Middleware,
    Router,
    callback_prefix,
    command,
    private_chat,
    text,
    update_type,
)
from tg_gateway.keyboards import InlineKeyboardBuilder, KeyboardError, paginate
from tg_gateway.ratelimit import InMemorySendLimiter, SendLimiter, TokenBucket
from tg_gateway.retry import RetryPolicy
from tg_gateway.transport import UpdateHandler, UpdateSource
from tg_gateway.transport.polling import PollingSource

__version__ = "0.1.0"

__all__ = [
    "BotClient",
    "CallbackCodec",
    "CallbackDataError",
    "CallbackDataTooLong",
    "Context",
    "Dispatcher",
    "Filter",
    "Handler",
    "InMemorySendLimiter",
    "InlineKeyboardBuilder",
    "InvalidCallbackData",
    "KeyboardError",
    "Middleware",
    "PollingSource",
    "RetryAfterHook",
    "RetryPolicy",
    "Router",
    "SendLimiter",
    "Timeouts",
    "TokenBucket",
    "UpdateHandler",
    "UpdateSource",
    "__version__",
    "callback_prefix",
    "command",
    "paginate",
    "private_chat",
    "text",
    "update_type",
]
