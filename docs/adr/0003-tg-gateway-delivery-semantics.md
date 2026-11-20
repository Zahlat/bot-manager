# ADR 0003: tg_gateway retry, delivery and security semantics

- Status: accepted
- Date: 2026-11-20

## Context
ADR 0001 chose to own a thin Telegram client. This ADR records how that client behaves, because
these rules are easy to break by accident and expensive to get wrong: a duplicate PDF, a leaked
token, or a lost update.

## Decision
- **Retries are decided by a pure `RetryPolicy`.** A 429 is retried for every method after
  `retry_after` plus jitter, because Telegram did not process the request; waits over 60 s are
  handed back to the caller. A 5xx or a network error after the request was sent is retried
  only for idempotent methods. `sendMessage` and `sendDocument` are retried only if the request
  never left (connect or pool errors). Anything else is raised at once.
- **The token never leaves the URL.** httpx exceptions are converted outside the `except` block
  (no `__context__` chain), errors keep only the method name, and the `httpx`/`httpcore` loggers
  are capped at WARNING because they log URLs. Tests inject failures that contain the token.
- **Send limits** live behind the `SendLimiter` Protocol: in-memory now, Redis in Phase 2.
  Chat sends acquire before every attempt, and every 429 penalizes that chat (or everyone).
- **callback_data is signed** with a truncated HMAC-SHA256 (`tgcb1` domain, 8-byte tag) and
  never truncated. Previous keys are accepted on decode so keys can be rotated.
- **Two kinds of middleware.** Dispatcher (outer) middleware runs for every update; Router
  (inner) middleware runs only around a matched handler. No error escapes `feed()` except
  cancellation, and logs carry the update_id only.
- **At-least-once delivery.** Polling advances the offset only after the handler returns and
  confirms it on stop or crash. The webhook dedupes by update_id and returns 503 (releasing the
  claim) when the hand-off fails, so Telegram redelivers. Handlers must be idempotent.
- **FastAPI is confined to `transport/webhook.py`** by an import-linter contract, so the client
  and dispatcher stay framework-free and reusable.

## Consequences
- Changing any rule above means changing a named test first (see `tests/**/tg_gateway`).
- Raising the `httpx` logger level is process-wide. That is intended: nothing should log URLs.
- Phase 2 replaces `InMemorySendLimiter` and `InMemoryUpdateDeduper` with Redis versions behind
  the same Protocols; the gateway itself does not change.
