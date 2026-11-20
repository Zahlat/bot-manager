"""Router (handlers + filters + inner middleware) and Dispatcher (outer middleware + routing).

- Dispatcher.middleware: *outer*. Runs for every update before routing, matched or not
  (user upsert, ban check, per-user rate limit). It can short-circuit by not calling next.
- Router.middleware: *inner*. Runs only around a matched handler, from the outer router inward.
- The first matching handler wins, searched depth-first in registration order.
- No exception from a handler, middleware or error handler escapes feed(), except cancellation.
  Logs carry the update_id only, never message content.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from tg_gateway.client import BotClient
from tg_gateway.dispatch.context import Context, ErrorHandler, Handler, Middleware
from tg_gateway.dispatch.filters import Filter
from tg_gateway.types import Update

__all__ = ["Dispatcher", "Router"]

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _Route:
    filters: tuple[Filter, ...]
    handler: Handler

    def matches(self, ctx: Context) -> bool:
        return all(f(ctx) for f in self.filters)


def _wrap(middleware: Middleware, call_next: Handler) -> Handler:
    async def call(ctx: Context) -> None:
        await middleware(ctx, call_next)

    return call


class Router:
    def __init__(self, name: str = "router") -> None:
        self.name = name
        self._entries: list[_Route | Router] = []
        self._middlewares: list[Middleware] = []
        self._error_handler: ErrorHandler | None = None
        self._has_parent = False

    def __repr__(self) -> str:
        return f"Router({self.name!r})"

    def handler(self, *filters: Filter) -> Callable[[Handler], Handler]:
        def register(fn: Handler) -> Handler:
            self._entries.append(_Route(filters, fn))
            return fn

        return register

    def middleware(self, middleware: Middleware) -> None:
        self._middlewares.append(middleware)

    def include_router(self, child: Router) -> None:
        if child is self or child._has_parent:
            raise ValueError(f"{child!r} is already included somewhere")
        child._has_parent = True
        self._entries.append(child)

    def on_error(self, handler: ErrorHandler) -> None:
        self._error_handler = handler

    def _find(
        self, ctx: Context, path: tuple[Router, ...]
    ) -> tuple[Handler, tuple[Router, ...]] | None:
        path = (*path, self)
        for entry in self._entries:
            if isinstance(entry, Router):
                found = entry._find(ctx, path)
                if found is not None:
                    return found
            elif entry.matches(ctx):
                return entry.handler, path
        return None


class Dispatcher:
    def __init__(self, router: Router, bot: BotClient) -> None:
        self._router = router
        self._bot = bot
        self._middlewares: list[Middleware] = []

    def middleware(self, middleware: Middleware) -> None:
        self._middlewares.append(middleware)

    async def feed(self, update: Update) -> bool:
        """Route one update. Returns True if a handler matched (even if it then failed)."""
        ctx = Context(update=update, bot=self._bot)
        matched = False

        async def route(c: Context) -> None:
            nonlocal matched
            found = self._router._find(c, ())
            if found is None:
                _log.debug("no handler for update %s", update.update_id)
                return
            handler, path = found
            matched = True
            chain = handler
            for router in reversed(path):
                for middleware in reversed(router._middlewares):
                    chain = _wrap(middleware, chain)
            try:
                await chain(c)
            except Exception as exc:  # isolate one bad update from the rest
                await self._handle_error(c, exc, path)

        call: Handler = route
        for middleware in reversed(self._middlewares):
            call = _wrap(middleware, call)
        try:
            await call(ctx)
        except Exception:
            _log.exception("middleware failed for update %s", update.update_id)
        return matched

    async def _handle_error(self, ctx: Context, exc: Exception, path: tuple[Router, ...]) -> None:
        for router in reversed(path):
            if router._error_handler is not None:
                try:
                    await router._error_handler(ctx, exc)
                except Exception:
                    _log.exception("error handler failed for update %s", ctx.update.update_id)
                return
        _log.error("unhandled error for update %s", ctx.update.update_id, exc_info=exc)
