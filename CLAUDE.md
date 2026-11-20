# Bot Manager — Syrian Curriculum PDF Bot

FastAPI service running a Telegram bot that serves PDF lessons/tests to Syrian-curriculum
students. Full plan: `docs/PLAN.md` (read only the section you need). Decisions: `docs/adr/`.

## Commands
- `uv sync --all-packages` — install everything (Python 3.13 via `.python-version`)
- `make check` — lint + typecheck + tests (what CI runs). Run before saying a task is done.
- `make lint` / `make format` / `make typecheck` / `make test` / `make security`
- `uv run pytest tests/unit/test_x.py -k name` — run one test
- `make up` / `make down` — Postgres 16 + Redis 7 (`docker-compose.dev.yml`)
- No `make` on Windows? Run the matching `uv run ...` line from the `Makefile`.

## Layout (uv workspace)
- `packages/platform_kit` — generic helpers. Imports no other workspace package.
- `packages/tg_gateway` — typed async Bot API client. May import only `platform_kit`.
- `packages/tg_flow` — flow-as-data engine. May import `tg_gateway`, `platform_kit`.
- `apps/curriculum_bot` — this bot: `domain/ application/ adapters/ presentation/ workers/ main.py`
- `tests/` — markers: unit, component, contract, integration, flow, golden, panel, chaos

## Dependency rules (enforced by `lint-imports`; never weaken a contract to make it pass)
- Reusable packages never import `curriculum_bot`.
- `domain` is pure Python: no pydantic, sqlalchemy, fastapi, httpx, redis, telegram.
- `application` depends only on `domain`; talks to the world through `Protocol` ports.
- `adapters`, `presentation`, `workers` don't import each other; `main.py` wires them.

## Code rules
- `mypy --strict` clean; no `Any` without a comment saying why. Ruff config in `pyproject.toml`.
- Async all the way: no blocking I/O in async code; one shared `httpx.AsyncClient`.
- Secrets only via `pydantic-settings` `SecretStr`. Never log URLs (the bot token is in them).
- All user-visible text is escaped for Telegram HTML. No `eval`, no SQL string building.
- Tests first for new behavior; don't edit an existing test to make code pass unless asked.
- New dependency → add it to the package that needs it (`uv add --package <name> <dep>`).

## Workflow
- Work one phase at a time (`docs/PLAN.md` → Execution phases); each phase has "Done" criteria.
- Never commit `.env`, never `git push`, never touch `migrations/versions/*` unless asked.
