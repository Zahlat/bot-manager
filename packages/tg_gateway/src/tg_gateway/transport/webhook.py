"""FastAPI webhook endpoint: verify, dedupe, hand off, ack. The only module importing FastAPI.

Order of checks (each failure returns an empty body: callers learn nothing about internals):
  1. secret header, constant-time            -> 401   (before reading the body)
  2. Content-Type is application/json         -> 415
  3. body size, streamed, before parsing      -> 413
  4. body parses as an Update                 -> 400
  5. dedupe claim (Redis SET NX in prod)      -> duplicate: 200, store down: 503
  6. hand-off (enqueue); failure releases     -> 503, so Telegram redelivers
  7. otherwise                                -> 200
"""

from __future__ import annotations

import hmac
import logging
import re
from collections import OrderedDict
from typing import Protocol

from fastapi import APIRouter, Request, Response
from pydantic import SecretStr, ValidationError

from tg_gateway.transport import UpdateHandler
from tg_gateway.types import Update

__all__ = ["InMemoryUpdateDeduper", "UpdateDeduper", "create_webhook_router"]

_log = logging.getLogger(__name__)

SECRET_HEADER = "x-telegram-bot-api-secret-token"  # noqa: S105  # a header name
_SECRET_RULE = re.compile(r"[A-Za-z0-9_-]{1,256}")  # Telegram's setWebhook secret_token rule
DEFAULT_MAX_BODY = 1_048_576


class UpdateDeduper(Protocol):
    async def claim(self, update_id: int) -> bool:
        """Mark the update as seen. False if it was already claimed."""
        ...

    async def release(self, update_id: int) -> None:
        """Undo a claim (the hand-off failed and Telegram will redeliver)."""
        ...


class InMemoryUpdateDeduper(UpdateDeduper):
    """Single-process deduper for dev/tests; production uses Redis with a TTL (Phase 2)."""

    def __init__(self, max_size: int = 10_000) -> None:
        self._seen: OrderedDict[int, None] = OrderedDict()
        self._max_size = max_size

    async def claim(self, update_id: int) -> bool:
        if update_id in self._seen:
            return False
        self._seen[update_id] = None
        if len(self._seen) > self._max_size:
            self._seen.popitem(last=False)
        return True

    async def release(self, update_id: int) -> None:
        self._seen.pop(update_id, None)


def _empty(status: int) -> Response:
    return Response(status_code=status)


async def _read_capped(request: Request, limit: int) -> bytes | None:
    """The body, or None as soon as it exceeds `limit` (never buffers more than that)."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        return None
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            return None
    return bytes(body)


def create_webhook_router(
    *,
    path: str,
    secret_token: SecretStr,
    on_update: UpdateHandler,
    dedupe: UpdateDeduper | None = None,
    max_body_bytes: int = DEFAULT_MAX_BODY,
) -> APIRouter:
    if not path.startswith("/") or len(path) < 2:  # noqa: PLR2004  # "/" plus at least one char
        raise ValueError("webhook path must start with '/' and not be the root")
    secret = secret_token.get_secret_value()
    if not _SECRET_RULE.fullmatch(secret):
        raise ValueError("secret_token must be 1-256 chars of A-Z a-z 0-9 _ -")
    expected = secret.encode("ascii")

    async def receive(request: Request) -> Response:  # noqa: PLR0911  # ordered checks
        given = request.headers.get(SECRET_HEADER, "").encode("latin-1")
        if not hmac.compare_digest(given, expected):
            return _empty(401)
        media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if media_type != "application/json":
            return _empty(415)
        body = await _read_capped(request, max_body_bytes)
        if body is None:
            return _empty(413)
        try:
            update = Update.model_validate_json(body)
        except ValidationError:
            return _empty(400)

        update_id = update.update_id
        if dedupe is not None:
            try:
                if not await dedupe.claim(update_id):
                    return _empty(200)  # already handled: ack so Telegram stops resending
            except Exception:
                _log.exception("dedupe store failed for update %s", update_id)
                return _empty(503)
        try:
            await on_update(update)
        except Exception:
            _log.exception("hand-off failed for update %s", update_id)
            if dedupe is not None:
                try:
                    await dedupe.release(update_id)
                except Exception:
                    _log.exception("could not release dedupe claim for update %s", update_id)
            return _empty(503)
        return _empty(200)

    router = APIRouter()
    router.add_api_route(
        path, receive, methods=["POST"], include_in_schema=False, response_class=Response
    )
    return router
