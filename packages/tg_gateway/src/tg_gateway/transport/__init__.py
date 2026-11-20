"""Where updates come from: long polling (dev) or a webhook (prod), behind one interface.

The webhook lives in ``tg_gateway.transport.webhook`` and is the only module that imports FastAPI.
It is deliberately not re-exported here, so importing tg_gateway never pulls in a web framework.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol

from tg_gateway.types import Update

__all__ = ["UpdateHandler", "UpdateSource"]

UpdateHandler = Callable[[Update], Awaitable[None]]


class UpdateSource(Protocol):
    async def run(self, handle: UpdateHandler, *, stop: asyncio.Event) -> None:
        """Deliver updates to `handle` until `stop` is set."""
        ...
