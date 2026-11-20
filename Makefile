# Single entry point for humans, CI and Claude. Keep recipes one-liners that also work on Windows.
COMPOSE = docker compose -f docker-compose.dev.yml

.PHONY: install lint format typecheck imports test test-all cov security up down logs ps check

install:            ## install all packages + dev tools, set up git hooks
	uv sync --all-packages
	uv run pre-commit install

lint:               ## ruff lint + format check + import contracts
	uv run ruff check .
	uv run ruff format --check .
	uv run lint-imports

format:             ## auto-fix lint and formatting
	uv run ruff check --fix .
	uv run ruff format .

typecheck:          ## mypy --strict
	uv run mypy

imports:            ## dependency rule only
	uv run lint-imports

test:               ## fast tests: unit + component + contract (+ flow, golden, panel)
	uv run pytest -m "not integration and not chaos" --cov --cov-report=term-missing

test-all:           ## everything, needs Docker
	uv run pytest --cov

security:           ## dependency audit (bandit rules run inside ruff "S")
	uv export --all-packages --no-emit-workspace --format requirements-txt -o .audit-reqs.txt
	uv run pip-audit --strict --disable-pip -r .audit-reqs.txt

check: lint typecheck test   ## what CI runs

up:                 ## start Postgres + Redis
	$(COMPOSE) up -d --wait

down:
	$(COMPOSE) down

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f
