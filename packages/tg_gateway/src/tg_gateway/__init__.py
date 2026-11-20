"""Typed async Telegram Bot API gateway. May import only platform_kit."""

from tg_gateway.client import BotClient, RetryAfterHook, Timeouts
from tg_gateway.retry import RetryPolicy

__version__ = "0.1.0"

__all__ = ["BotClient", "RetryAfterHook", "RetryPolicy", "Timeouts", "__version__"]
