# ADR 0001: Hexagonal architecture with reusable packages

- Status: accepted
- Date: 2026-11-19

## Context
The bot must be testable without Telegram, Postgres or Redis. The Telegram client and
generic helpers must be reusable in future bots unchanged.

## Decision
- Four packages in one uv workspace: `platform_kit` <- `tg_gateway` <- `tg_flow` <- `curriculum_bot`.
  Arrows are the only allowed import direction.
- Inside `curriculum_bot`: `domain` <- `application` <- (`adapters` | `presentation` | `workers`) <- `main`.
  Use cases depend on ports (`typing.Protocol`); adapters implement them; `main.py` is the only
  place that wires concrete classes together.
- We own a thin `tg_gateway` client on httpx instead of aiogram / python-telegram-bot (~15 methods needed).

## Consequences
- Both rules are enforced by import-linter contracts in `pyproject.toml` (`make lint` / CI).
- Use cases are tested against in-memory fakes with no I/O.
- More upfront code than a framework; aiogram can still be put behind the gateway later.
