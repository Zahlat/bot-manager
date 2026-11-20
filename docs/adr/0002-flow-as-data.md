# ADR 0002: The conversation flow is data, capabilities are code

- Status: accepted
- Date: 2026-11-19

## Context
Admins must change screens, buttons, commands and texts without a deploy, but must never be
able to run arbitrary code.

## Decision
- The flow is an immutable, versioned JSON document (Pydantic discriminated union on `type`,
  with `schema_version`), stored in `flow_versions`. Draft -> validate -> publish; rollback =
  re-publish an old version.
- Code provides a fixed registry of screen types and actions (`tg_flow.registry`); the app
  registers its own (catalog browser, resource list).
- Visibility rules are JSON Specifications evaluated without `eval`. Texts use an allowlist of
  placeholders, no template logic, and are escaped for Telegram HTML.

## Consequences
- A validator must reject broken flows before publish (missing targets, unreachable screens,
  limits, unknown actions/placeholders, non-https or non-allowlisted URLs).
- The runtime keeps the compiled flow in memory, invalidated via Redis pub/sub on publish.
