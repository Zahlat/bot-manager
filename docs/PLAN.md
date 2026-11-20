# Syrian Curriculum PDF Bot — Execution Plan

Nov 19, 2026 · @Salman

## Overview

Build a FastAPI service that runs a Telegram bot for Syrian-curriculum students. Students browse stage, grade, track and subject, and the bot sends them PDF lessons, tests and past exam samples. The code is split into two reusable packages (a Telegram gateway and a generic platform kit) and one app package for this bot. You can move the two reusable packages into any future bot unchanged.

**Scope (v1):** a free bot whose whole conversation (screens, buttons, commands, texts) is configured by admins in a web admin panel. Through it they manage the curriculum from 1st primary to the baccalaureate, every file and its visibility, broadcasts and stats. Students browse, search and receive PDFs; favorites and subscriptions are features the admin can switch on or off.

**Assumptions** (change any of these and the plan adjusts):

- The bot's interface is in Arabic, with an English fallback. Admins are a few trusted people known by their Telegram user ID. The bot is completely free for students, with no payments or access gating.
- Both the catalog and the conversation flow are stored as data rather than code, so admins can change the curriculum tree and the bot's dialogue without a deploy.
- Every PDF is under Telegram's 50 MB bot upload limit; larger files are split before upload.
- You hold the rights to share the content you upload.
- Expected load is in the thousands of students, with spikes before exams. The design scales out later; v1 runs on one VM.

**Stack**

| Concern | Choice | Why |
| --- | --- | --- |
| Runtime | Python 3.12+, `uv` for dependencies and lockfile | Fast and reproducible |
| HTTP | FastAPI + Uvicorn (uvloop, httptools) | Required; async-first |
| Telegram transport | Your own thin `tg_gateway` client on `httpx` | Reusable across bots; the app does not depend on a framework (see Architecture) |
| Conversation engine | Your own `tg_flow` package: flow-as-data interpreter | Admin-controlled dialogue with no hard-coded flow; reusable across bots |
| Admin panel | Server-rendered Jinja2 + HTMX, Telegram Login Widget | One deployable, server-side authorization, strict CSP |
| Validation | Pydantic v2, `pydantic-settings` | Typed updates, typed config, typed flow documents |
| Database | PostgreSQL 16, SQLAlchemy 2.0 async + `asyncpg`, Alembic | Transactions, JSONB flow versions, full-text and trigram search |
| Cache / queue | Redis 7, plus `arq` for background jobs | Idempotency keys, rate limits, sessions, flow cache invalidation, broadcast jobs |
| File storage | S3-compatible object storage (OCI Object Storage or Cloudflare R2), local disk in dev | Keeps an original copy of every PDF outside Telegram |
| Observability | `structlog` JSON logs, Prometheus metrics, OpenTelemetry traces (optional) | Debuggable in production |
| Quality | `ruff`, `mypy --strict`, `pytest`, Playwright (panel), pre-commit, GitHub Actions | The senior-quality baseline |

## Architecture

The architecture is clean (hexagonal): dependencies point inward only. The domain and use cases know nothing about Telegram, FastAPI, Postgres or Redis. Those are adapters plugged in at a single composition root. This boundary is also what makes the gateway reusable, since the bot's app code depends on a `Messenger` port rather than on Telegram directly.

&#91;embedded content: architecture · 3 packages, 3 app layers, 4 adapters\]

Handlers call use cases, use cases call ports, and adapters implement those ports. The three highlighted packages sit underneath and never import from the app.

**Four packages in one repository (a uv workspace):**

1. **`tg_gateway` (reusable, Telegram-specific).** A typed async Bot API client covering only the methods you use. It includes Pydantic models for updates, an error hierarchy (`TelegramRetryAfter`, `TelegramForbidden` when a user blocks the bot, `TelegramBadRequest`), a retry policy that honors `retry_after`, per-chat and global rate limiters, two transports (a FastAPI webhook router factory and a long-polling runner, both behind one `UpdateSource` interface), a router/dispatcher with filters and middleware, keyboard builders and a signed-callback-data codec. It has no database and knows nothing about this bot.
2. **`tg_flow` (reusable, conversation engine).** The flow schema (screens, buttons, actions, commands, rules), the publish-time validator, the runtime interpreter and renderer, and registries where an app plugs in its own screen types and actions. It is built on `tg_gateway` and knows nothing about curricula (see Configurable conversation flow).
3. **`platform_kit` (reusable, generic).** Settings loading, structured logging that redacts secrets, request/correlation IDs, a retry decorator with backoff and jitter, a circuit breaker, a Redis idempotency store, a token-bucket limiter, feature flags, health and readiness checks, a Prometheus metrics middleware, an async DB session/unit-of-work base, and security helpers (constant-time compare, HMAC signing, Argon2 hashing, CSRF, session cookies).
4. **`curriculum_bot` (this app).** Domain, application, adapters, presentation (the admin panel, the bot's screen types and actions) and the composition root.

**Why build the client instead of using aiogram or python-telegram-bot:** you need roughly 15 Bot API methods. A thin client you own keeps FastAPI in charge of HTTP, avoids lock-in to a framework's dispatcher and DI, and is the part you will carry into the next bot. If scope grows a lot (payments, inline mode, business accounts), you can put aiogram behind the same gateway interface without touching the app.

**Repository layout**

```
repo/
├─ packages/
│  ├─ tg_gateway/src/tg_gateway/
│  │  ├─ client.py          # BotClient: send_message, send_document, answer_callback_query…
│  │  ├─ types/             # Pydantic Update, Message, CallbackQuery, Document…
│  │  ├─ errors.py          # error hierarchy mapped from Bot API responses
│  │  ├─ transport/         # webhook.py (FastAPI router factory), polling.py
│  │  ├─ dispatch/          # Router, filters, middleware chain, handler registry
│  │  ├─ ratelimit.py       # global ~30 msg/s bucket + per-chat 1 msg/s
│  │  ├─ keyboards.py       # inline keyboard builder, pagination helper
│  │  └─ callback.py        # compact, HMAC-signed callback_data codec (≤64 bytes)
│  ├─ tg_flow/src/tg_flow/
│  │  ├─ schema.py          # FlowDocument, Screen union, Button, Command, Rule (Pydantic)
│  │  ├─ validate.py        # graph + limits + placeholder + URL checks before publish
│  │  ├─ runtime.py         # interpreter: (session, update) -> screen -> render -> send
│  │  ├─ render.py          # text templating (allowlisted placeholders), keyboards
│  │  ├─ rules.py           # visibility rule evaluator (Specification, no eval)
│  │  ├─ registry.py        # ScreenType and Action registries (plugin points)
│  │  └─ store.py           # FlowStore port: load published, draft, versions
│  └─ platform_kit/src/platform_kit/
│     ├─ config.py  logging.py  flags.py  resilience/ (retry, breaker, timeout)
│     ├─ idempotency.py  ratelimit.py  cache.py  health.py  metrics.py
│     ├─ db/ (engine, UnitOfWork base)  security/ (hmac, hashing, csrf, sessions, redaction)
├─ apps/curriculum_bot/src/curriculum_bot/
│  ├─ domain/          # entities, value objects, domain errors (pure Python)
│  ├─ application/     # use cases, ports (Protocols), DTOs
│  ├─ adapters/        # sqlalchemy repos + flow store, redis, s3 storage, telegram messenger
│  ├─ presentation/
│  │  ├─ bot/           # screen types (catalog, resource list) + actions registered into tg_flow
│  │  └─ admin_panel/   # FastAPI routes, Jinja2 templates, HTMX partials, flow editor JS
│  ├─ workers/         # arq tasks: process_update, broadcast, ingest_pdf, publish_flow
│  └─ main.py          # composition root: create_app(), DI wiring, lifespan
├─ migrations/  seeds/ (default curriculum + default flow)  tests/  deploy/  .claude/  CLAUDE.md
```

**How one update flows through the system**

1. Telegram POSTs the update to `/tg/{random_path}`. The webhook router checks the `X-Telegram-Bot-Api-Secret-Token` header in constant time, checks body size and content type, and parses the body into an `Update`.
2. The router claims `update_id` in Redis (`SET NX`, 24 h TTL). A duplicate gets an immediate 200 and is dropped.
3. The update is enqueued to `arq`, and the endpoint returns 200, typically in a few milliseconds. Telegram only needs the acknowledgement.
4. A worker runs the middleware chain (logging context, user upsert, ban check, per-user rate limit, language), then hands the update to the `tg_flow` runtime.
5. The runtime loads the user's session and the in-memory published flow, decodes and verifies the callback data, then resolves the target screen or action. If it is an action, the action calls a use case such as `SendResource(user_id, resource_id)`, which checks visibility and authorization through ports.
6. The runtime renders the next screen (text, visible buttons only) and edits the current message, so the chat stays clean and the per-chat limit is respected. For a file, the Telegram adapter sends the cached `file_id`. A 429 is retried after `retry_after`, and a 403 marks the user inactive.
7. A download event is recorded and metrics are incremented.

In local development, step 1 is replaced by the long-polling transport, so you don't need a public URL or HTTPS.

## Configurable conversation flow

The rule is **flow is data, capabilities are code**. Admins build the entire conversation in the panel: screens, buttons, messages, commands, order, visibility and which features are on. The code provides only a fixed set of screen types and actions that the admin assembles. The bot interprets the published flow at runtime. No dialogue is hard-coded, and admins can't execute arbitrary code, which keeps the bot both flexible and safe.

The engine lives in a fourth package, **`tg_flow`**, which is reusable and bot-agnostic. It defines the flow schema, validator, runtime interpreter, renderer and the registries for screen types and actions. `curriculum_bot` only registers its domain-specific screen types (catalog browser, resource list), so the next bot reuses the engine and registers its own.

**Building blocks the admin composes**

| Element | What the admin configures | Provided by |
| --- | --- | --- |
| **Screen: Message** | Text (Arabic/English) with safe placeholders such as `{first_name}`, optional image or file, buttons | `tg_flow` |
| **Screen: Menu** | Title text plus a grid of buttons; each button goes to another screen, runs an action, or opens a URL | `tg_flow` |
| **Screen: Input** | A question, the expected input type (text, number, choice) and where the answer goes (search, feedback) | `tg_flow` |
| **Screen: Catalog browser** | Which category subtree to show (for example all stages, or only Secondary), columns, sort order, empty-state text | `curriculum_bot` |
| **Screen: Resource list** | Category, resource types, page size, sort (newest, title, year), list style | `curriculum_bot` |
| **Actions** | Pick from a registry: send resource, search, add favorite, subscribe, report file, show my data, delete my data, go back, go home | Code (registry), selected by admin |
| **Commands** | Map `/start`, `/help`, `/search` and any custom command to a screen; descriptions are pushed to Telegram with `setMyCommands` on publish | `tg_flow` |
| **Visibility rules** | Show or hide a screen or button by language, feature flag, date window (for example "exam season") or user segment | `tg_flow` (Specification pattern, JSON rules, no `eval`) |
| **Feature flags** | Turn search, favorites, subscriptions or reports on or off globally | `platform_kit` flags, read by rules |

**How a flow is stored and run**

- A flow version is one immutable JSON document validated by Pydantic: a discriminated union on `type` per screen, plus a `schema_version` so the format can evolve with migrations. Editing happens on a **draft**; **publishing** freezes a new version, and **rollback** re-publishes an older one in one click.
- Before publish, the validator rejects broken flows. It checks for missing targets, screens unreachable from `/start`, more than 100 buttons on a screen, labels that are too long, unknown actions, placeholders not in the allowlist, and URL buttons that aren't `https` or aren't on the allowed-domain list.
- At runtime, each process holds the published flow compiled in memory (screens indexed by ID). A Redis `flow:version` key is checked per update, and a pub/sub message on publish swaps the in-memory flow at once, so there is no DB read per update.
- Callback data stays tiny and signed: `{screen_id}:{action}:{args}` plus an HMAC. Screen IDs are short integers assigned at publish.
- A user's position (current screen, pending input) lives in Redis with a TTL, so a stale button from an old flow version falls back to the home screen instead of erroring.
- **Admin preview:** admins can switch their own bot session to the draft (`/preview`) and use it in real Telegram before publishing. The panel also has a simulator that renders any screen as students would see it.

&#91;embedded content: flow change lifecycle · 5 steps, 2 loops\]

Example of one screen in the flow document:

```json
{
  "id": "secondary_home", "type": "menu",
  "text": {"ar": "اختر الصف", "en": "Choose your grade"},
  "buttons": [
    {"label": {"ar": "البكالوريا العلمي"}, "goto": "catalog_bac_sci"},
    {"label": {"ar": "نماذج امتحانات"}, "goto": "exam_samples",
     "visible_if": {"all": [{"feature": "exam_samples"}, {"date_between": ["2027-04-01", "2027-07-15"]}]}},
    {"label": {"ar": "بحث"}, "action": "search"},
    {"label": {"ar": "رجوع"}, "action": "back"}
  ],
  "columns": 2
}
```

## Admin panel

The admin panel is a web app served by the same FastAPI service under `/admin`. It is server-rendered with Jinja2 and HTMX, so there is no separate frontend build to deploy or secure. From it, admins manage the whole curriculum (1st primary through the secondary baccalaureate), every file and its visibility, and the entire bot conversation.

**Why server-rendered + HTMX rather than a React SPA:** the panel is forms, tables, trees and previews. HTMX gives partial-page updates without a JavaScript build chain, keeps all authorization on the server, and makes a strict Content-Security-Policy easy. The flow editor is the one rich page, so it uses a small, self-contained JS component; a drag-and-drop graph editor (for example React Flow) can be added later on that page alone. The same panel can also open as a Telegram Mini App for admins on phones.

**Authentication and roles**

- Login with the **Telegram Login Widget**. The server verifies the HMAC-SHA256 hash (key = SHA-256 of the bot token), rejects `auth_date` older than 5 minutes, and requires the Telegram ID to be in `admins`. There are no passwords to leak. The owner role can also be required to pass a TOTP code.
- Server-side sessions in Redis with an `HttpOnly; Secure; SameSite=Strict` cookie, 8-hour idle timeout, CSRF token on every form, strict CSP with nonces, login rate limit, optional IP allowlist at Caddy.
- Roles: **owner** (everything, including admins and publishing the flow), **publisher** (content + publish), **editor** (draft content and flow, can't publish), **viewer** (stats only). The role check runs in the use cases, so the panel, the bot and the API all enforce the same rules.

**Pages**

| Page | What the admin can do |
| --- | --- |
| Dashboard | Daily active students, downloads today and over 7 days, top resources, top zero-result searches, open reports, broadcast progress |
| Curriculum | Edit the full tree: stages, grades, tracks, subjects. Add, rename (Arabic/English), reorder by drag, hide or show, move, and set an icon or emoji per node. Seeded with a default Syrian tree you review once (see below) |
| Files | Drag-and-drop multi-upload with progress, metadata form, PDF preview, duplicate warning, replace a file while keeping its link. Bulk edit, bulk move, ZIP + CSV import |
| Visibility | Per file: draft, published or hidden; scheduled publish/unpublish dates; pinned or featured; sort order; which resource types appear under each subject. Per category: show or hide a whole branch (for example hide a grade until its files are ready) |
| Bot flow | Screen list and tree view, screen editor (text, buttons, targets, actions, rules), commands editor, feature flags, validator results, simulator preview, draft/publish/rollback with a diff between versions |
| Messages | Every system text (errors, empty states, rate-limit notice) in Arabic and English, editable with placeholder checks |
| Broadcasts | Compose with preview, choose a segment (everyone, a grade, a subject's subscribers), schedule, watch live progress, pause or cancel |
| Students | Search by ID, see activity, ban or unban, segment counts. Personal data shown is minimal by design |
| Reports | Broken or wrong file reports with one-click fix (replace file, hide, reply to the student) |
| Admins and audit | Invite admins by Telegram ID, set roles, view the full audit log (who changed what, with a diff) |
| Settings | Bot profile texts (description, about), allowed URL domains, rate limits, retention period |

**Default curriculum seed** (you confirm or edit it in the panel; nothing about the tree is fixed in code):

| Stage | Grades | Notes |
| --- | --- | --- |
| Basic education, cycle 1 (primary) | 1st to 6th | One subject list per grade |
| Basic education, cycle 2 (preparatory) | 7th to 9th | 9th grade = the basic education certificate exam year |
| Secondary | 10th, 11th, 12th | 11th and 12th split into scientific and literary tracks; 12th = baccalaureate. Other branches (vocational, Sharia) can be added as nodes |

Subjects per grade and track are entered once (or imported from a CSV) and then managed in the panel.

## Design patterns

Each pattern below is here to solve a specific problem in this bot. None is used just for its own sake.

| Pattern | Where | Problem it solves |
| --- | --- | --- |
| Ports & Adapters (Hexagonal) | `application/ports.py` as `typing.Protocol`s; `adapters/` implements them | Swap Telegram, Postgres or storage without touching business rules; test use cases with in-memory fakes |
| Composition Root + constructor DI | `main.py` `create_app()` wires everything once; FastAPI `Depends` only at the edge | No global singletons; each test builds its own graph |
| Application Factory | `create_app(settings)` | The same app runs in prod, tests and polling mode with different settings |
| Use Case / Command Handler (CQRS-lite) | `application/use_cases/*.py`, one class per action | Thin handlers; business logic in one place; commands (upload, publish) separated from queries (browse, search) |
| Repository + Unit of Work | `adapters/db/` | Transaction boundaries are explicit; domain stays free of SQLAlchemy |
| Value Objects | `ResourceId`, `Sha256`, `TelegramUserId`, `NormalizedArabicText` | Invalid states can't be represented; validation lives in one place |
| Router + Chain of Responsibility | `tg_gateway.dispatch`: middleware chain, then filter-matched handler | Logging, auth, rate limits and i18n are written once instead of in every handler |
| Strategy | `UpdateSource` (webhook / polling), `FileStorage` (local / S3), `Cache` (Redis / in-memory) | Swap behavior by config |
| Adapter | `TelegramMessenger` implements the `Messenger` port using `tg_gateway.BotClient` | The app never imports Telegram types |
| Decorator | `@retry`, `@circuit_breaker`, `@timeout` in `platform_kit.resilience` | Resilience is applied declaratively, not tangled with logic |
| Token Bucket | `ratelimit.py` (Redis-backed for multi-worker) | Stay under Telegram limits; throttle abusive users |
| Circuit Breaker | Around the Bot API and object storage | Fail fast while a dependency is down instead of piling up timeouts |
| Idempotent Consumer | `update_id` dedupe; SHA-256 dedupe on uploads | Telegram redeliveries and double uploads are harmless |
| Transactional Outbox | `broadcasts` + `broadcast_targets` with a cursor | Announcements survive restarts and never double-send |
| Finite State Machine | Admin upload conversation (choose subject, then type, then title, then confirm), state in Redis with a TTL | Multi-step flows without fragile ad-hoc flags |
| Builder | `InlineKeyboardBuilder`, paginated menu builder | Readable menu construction; enforces button and callback limits |
| Specification | `ResourceFilter` (category, type, year, status) | Composable queries for browse and search without SQL in use cases |

Patterns added for the admin-configurable flow:

| Pattern | Where | Problem it solves |
| --- | --- | --- |
| Interpreter | `tg_flow.runtime` executes the flow document | The dialogue is data the admin edits; code only interprets it |
| Registry / Plugin | `ScreenType` and `Action` registries in `tg_flow`; the app registers catalog and resource types | New capabilities are added in code once, then admins can use them anywhere in the flow |
| Specification (rules) | `visible_if` JSON rules evaluated by `tg_flow.rules` | Conditional buttons and screens without `eval` or scripting |
| Immutable snapshot (Memento) | Each published flow version is a frozen JSON document | One-click rollback and a diff between any two versions |
| Observer (pub/sub) | Publish emits `flow.published` on Redis; every process swaps its cached flow | Changes go live instantly with no DB read per update |
| Template (safe) | Placeholder rendering with an allowlist, no template engine logic | Admin-editable texts can't run code or inject markup |

## Features

The MVP is browsing and sending PDFs, managed entirely from the admin panel. The bot is free: every feature is available to every student, with no payments or gating. All student screens below ship as the **default flow** seeded at install, and admins can reword, reorder, hide or disable each one.

**Student-facing**

| Feature | Release | Notes |
| --- | --- | --- |
| `/start`, language choice (Arabic default), short help | MVP | Creates the user row; deep links such as `t.me/bot?start=res_123` open a specific resource |
| Browse catalog: Stage, then Grade, then Track (secondary), then Subject, then Resource type | MVP | Inline keyboards with Back and Home buttons; resource types: lessons, summaries, tests, exam samples, other |
| Paginated resource list and tap-to-receive PDF | MVP | Keyset pagination of 8 items per page; the file is sent by cached `file_id` |
| Search by keyword | MVP | Arabic normalization (diacritics, alef/ya/ta-marbuta variants), Postgres `pg_trgm` + full-text search |
| Favorites and "recently received" | v1.1 | Quick re-access before exams |
| Subscribe to a subject or grade to get notified of new uploads | v1.1 | Uses the throttled broadcast worker |
| Report a broken or wrong file | v1.1 | Creates a ticket that admins see |
| `/mydata` and `/deleteme` | MVP | Privacy: show or erase stored data (many users are minors) |

**Admin-facing**

Admin work happens in the web **Admin panel** (see that section): curriculum, files and visibility, bot flow, messages, broadcasts, students, reports, admins and settings. The bot itself keeps two admin shortcuts for phones: forwarding a PDF to the bot starts a quick upload (it lands as a draft), and `/preview` switches the admin's own session to the draft flow.

## Data model

The curriculum is a self-referencing category tree. Telegram `file_id`s live in their own table keyed by bot, because a `file_id` only works for the bot that obtained it. Keeping them separate is what lets a second bot reuse the same catalog.

| Table | Key columns | Notes |
| --- | --- | --- |
| `categories` | `id`, `parent_id`, `kind` (stage, grade, track, subject), `name_ar`, `name_en`, `slug`, `sort_order`, `is_active` | Adjacency list plus a materialized `path` for fast breadcrumb and subtree queries |
| `resources` | `id`, `category_id`, `type` (lesson, summary, test, exam\_sample, other), `title`, `title_norm`, `year`, `term`, `status` (draft, published, archived), `sha256` (unique), `size_bytes`, `page_count`, `storage_key`, `created_by`, `created_at`, `published_at` | GIN trigram index on `title_norm`; partial index `WHERE status='published'` |
| `telegram_files` | `resource_id`, `bot_key`, `file_id`, `file_unique_id`, `created_at` | PK (`resource_id`, `bot_key`); refilled lazily if a `file_id` stops working |
| `users` | `telegram_id` (PK), `language`, `first_seen_at`, `last_seen_at`, `is_blocked_bot`, `is_banned` | Deliberately minimal: no phone number and no username history |
| `admins` | `telegram_id`, `role` (owner, editor), `added_by`, `added_at` | Changes are written to `audit_log` |
| `subscriptions` / `favorites` | `user_id`, `category_id` or `resource_id`, `created_at` | Composite PKs |
| `download_events` | `id`, `user_id`, `resource_id`, `at`, `via` (browse, search, deeplink) | Append-only; monthly partitions once it grows |
| `broadcasts` / `broadcast_targets` | `id`, `segment`, `payload`, `status`, `cursor`, counts / `broadcast_id`, `user_id`, `state` | The outbox; resumable |
| `audit_log` | `id`, `actor_id`, `action`, `entity`, `entity_id`, `diff` (JSONB), `at` | Every admin action |
| `reports` | `id`, `user_id`, `resource_id`, `reason`, `status` | Broken-file reports |

Tables and columns added for the admin panel and the configurable flow:

| Table / change | Key columns | Notes |
| --- | --- | --- |
| `resources` (added columns) | `visibility` (draft, published, hidden, archived), `publish_at`, `unpublish_at`, `pinned`, `sort_order` | A scheduler job applies publish and unpublish dates; students only ever see `published` within the window |
| `categories` (added columns) | `is_visible`, `icon`, `allowed_resource_types` | Hiding a branch hides everything under it |
| `flow_versions` | `id`, `bot_key`, `version`, `status` (draft, published, archived), `document` (JSONB), `schema_version`, `checksum`, `created_by`, `published_by`, `published_at` | One draft per bot; published rows are immutable; rollback = publish a copy of an old version |
| `feature_flags` | `key`, `enabled`, `updated_by`, `updated_at` | Read through a cached flag service |
| `system_texts` | `key`, `ar`, `en`, `placeholders` | Error and empty-state texts editable in the panel |
| `admin_sessions` (Redis) | `session_id` to `admin_id`, role, CSRF secret, expiry | Server-side; revocable instantly |

Redis keys include `upd:{update_id}` (dedupe), `rl:user:{id}` and `rl:chat:{id}` (buckets), `fsm:{admin_id}` (upload conversation), and `cat:v{n}:{category_id}` (versioned menu cache, so publishing a resource bumps `n` and invalidates the cache).

## Performance

The biggest performance gain is free: upload each PDF to Telegram once, then always send it by `file_id`. Telegram's [Bot FAQ](https://core.telegram.org/bots/faq) says `file_id`s can be treated as persistent. That means a send moves a few bytes instead of up to 50 MB, and your server's bandwidth stays near zero during exam rushes.

**Telegram limits the design has to respect** (from the [Bot FAQ](https://core.telegram.org/bots/faq)):

| Limit | Value | Design response |
| --- | --- | --- |
| Messages to one chat | about 1 per second (short bursts tolerated, then 429s) | Per-chat bucket in the gateway; menu edits use `editMessageText` instead of sending new messages |
| Bulk notifications | about 30 per second across users; paid broadcasts up to 1,000 per second | Broadcast worker at 25/s (headroom), spread over hours, with a separate bulkhead from interactive replies |
| Groups | 20 messages per minute | Not used in v1 (private chats only) |
| Send a file | 50 MB | Validation rejects or splits larger files |
| Download via `getFile` | 20 MB | Admin uploads over 20 MB go through the admin API or object storage, not bot download |

**Techniques, in order of impact**

1. **`file_id` reuse.** Upload once, ideally to a private "storage" channel the bot administers, record `file_id` per bot, and send by ID afterwards.
2. **Fast-ack webhooks.** Verify, dedupe, enqueue and return 200. Slow work never blocks Telegram's delivery or holds a request slot.
3. **Connection reuse.** One long-lived `httpx.AsyncClient` per process with keep-alive and a bounded pool. Never create a client per request.
4. **Menu cache.** Catalog levels are cached in Redis (and in-process with a short TTL) under versioned keys, so browsing does no DB queries in the common case.
5. **Lean DB access.** Async `asyncpg` pool sized to the worker count, keyset pagination, partial and trigram indexes, `selectinload` to avoid N+1 queries, `EXPLAIN ANALYZE` on every new query in review.
6. **Fast runtime.** `uvloop` + `httptools`, `orjson` responses, Pydantic v2 models, no sync I/O in async paths (enforced by a ruff rule and review).
7. **Bulkheads.** Separate semaphores and queues for interactive replies and broadcasts, so an announcement never slows a student's download.
8. **Horizontal scale later.** The web and worker processes are stateless (state lives in Postgres and Redis), so you add workers or VMs without code changes.

**Performance targets** (set by this plan, verified by the load tests in Testing):

| Metric | Target |
| --- | --- |
| Webhook ack latency p95 | under 50 ms |
| Update to `sendDocument` call issued, p95 (cache warm) | under 300 ms |
| Sustained interactive throughput on the free VM | 200 updates per second with no errors |
| Broadcast to 10,000 users | finishes in about 7 minutes at 25/s with no 429 storms |

## Security

Security is never traded for speed. Every control below is a v1 requirement, each one is tested, and the security review in Phase 7 checks the list item by item against the OWASP API Security Top 10.

**1. Secrets**

- The bot token, webhook secret, DB/Redis passwords, HMAC keys and S3 keys come only from environment variables or a secrets file mounted read-only (`pydantic-settings` with `SecretStr`). Nothing is committed; `.env` is git-ignored, and `gitleaks` runs in pre-commit and CI.
- The bot token appears in Bot API URLs, so the gateway never logs URLs. httpx debug logging is disabled, and a `structlog` processor redacts anything shaped like `\d+:[A-Za-z0-9_-]{30,}`.
- Rotation runbook: revoke in @BotFather, update the secret, restart, re-run `setWebhook`.

**2. Webhook endpoint**

- `setWebhook` gets a random `secret_token`, which Telegram sends back on every request in the `X-Telegram-Bot-Api-Secret-Token` header. It must be 1 to 256 characters from `A-Z a-z 0-9 _ -` ([Hookdeck guide](https://hookdeck.com/webhooks/platforms/guide-to-telegram-webhooks-features-and-best-practices)). Compare it with `hmac.compare_digest` and return 401 on mismatch.
- The URL path is also random (`/tg/<32 random chars>`). That is defense in depth, not the main control.
- Optional IP allowlist at the reverse proxy for Telegram's ranges `149.154.160.0/20` and `91.108.4.0/22` (same source).
- The endpoint caps body size (1 MB), requires `application/json`, parses strictly with Pydantic, and returns no error details.
- `allowed_updates` is limited to what you handle (`message`, `callback_query`, `my_chat_member`).

**3. Untrusted input**

- Every update field is untrusted. `callback_data` (64-byte limit) carries a compact action, IDs and a truncated HMAC, and the codec rejects tampered payloads. Even with a valid signature, the use case re-checks authorization, for example whether the resource is published.
- Search text is length-capped, normalized and passed as bound parameters. Raw SQL is never built from strings.
- All user-visible text is escaped for Telegram's HTML parse mode, so admin-entered titles can't inject markup.

**4. Authorization**

- Roles are enforced in use cases (`require_role(actor, Role.EDITOR)`), not only in handlers, so the admin API and bot share one check. Tests cover the full authorization matrix (role by action).
- Admin add/remove requires the owner role and is written to `audit_log`.

**5. Uploaded PDFs**

- Validation pipeline: magic bytes `%PDF-`, size of 50 MB or less, a page-count limit, parse with `pypdf` (reject encrypted or malformed files), and flag or reject PDFs that contain JavaScript, launch actions or embedded files. The file is stored under a generated key, never the user's filename. Optional ClamAV scan.
- SHA-256 dedupe also stops replay of an already-rejected file.

**6. Admin REST API**

- Authentication with short-lived JWTs or hashed API keys (Argon2), plus an IP allowlist and its own rate limit. `/docs` and `/openapi.json` are disabled in production, CORS is closed, and security headers are set at the proxy.
- Pydantic request models use `extra='forbid'`, and response models never leak internal fields.

**7. Abuse and privacy**

- Per-user token bucket (for example 20 actions per minute), escalating temporary bans and a ban list.
- Data minimization, because many users are minors: store only the Telegram ID, language and timestamps. Provide `/deleteme`, keep download events for a limited time (for example 12 months), and never share data.

**8. Supply chain and runtime**

- Locked dependencies (`uv.lock`), Dependabot, `pip-audit`, and `bandit` and `ruff` security rules in CI.
- Container: slim base, non-root user, read-only root filesystem, `no-new-privileges`, all capabilities dropped, and Trivy image scans in CI.
- Host: SSH keys only with password login and root login disabled, firewall open only on 443 (and 22 from your IP), automatic security updates, fail2ban. Postgres and Redis listen only on the internal Docker network, and Redis requires a password.

**9. Admin panel and admin-controlled flow**

- The panel is the largest attack surface, so it gets the strictest controls: Telegram Login hash verified server-side with a 5-minute `auth_date` window, server-side sessions, `SameSite=Strict` cookies, CSRF tokens, strict CSP with nonces, `X-Frame-Options: DENY`, login rate limiting, and optional TOTP and IP allowlist for the owner.
- Every panel route declares its required role. A test enumerates all routes and fails if any route lacks a role guard.
- Admin-entered text is data, never code. Placeholders come from an allowlist (`{first_name}`, `{grade}`), there is no template logic, output is escaped for Telegram HTML, and the panel escapes everything it displays.
- URL buttons accept only `https` links on an allowed-domain list, so a compromised editor account can't plant phishing links. Publishing the flow needs the publisher or owner role, every publish is audited with a diff, and rollback is one click.
- Uploads from the panel go through the same PDF validation pipeline as bot uploads, with streaming size limits enforced before the body is fully read.

## Robustness and resilience

Every failure mode has a planned response, and each of those responses is covered by a resilience test.

| Failure | Detection | Response |
| --- | --- | --- |
| Telegram redelivers an update (we were slow, or it retried) | `update_id` already in Redis | Return 200 and drop it; handlers are idempotent anyway |
| Bot API returns 429 | `TelegramRetryAfter(retry_after)` | Sleep exactly `retry_after` (plus jitter), then retry; the global bucket backs off for everyone |
| Bot API 5xx or network error | httpx exception or status | Exponential backoff with full jitter, at most 4 attempts, only for idempotent sends; then the circuit breaker opens for 30 s |
| User blocked the bot (403) | `TelegramForbidden` | Mark `is_blocked_bot`, skip in broadcasts, never retry |
| Stale `file_id` (400 with a file error) | `TelegramBadRequest` matched | Re-upload from object storage, store the new `file_id`, send |
| Redis down | Health check, connection errors | Readiness fails, so the webhook returns 503 and Telegram retries later; no updates are lost |
| Postgres down | Health check | Same as Redis; menus are still served from the in-process cache for reads |
| Worker crash mid-broadcast | Job heartbeat lost | Outbox cursor resumes on restart; `broadcast_targets.state` prevents duplicates |
| Deploy or restart | SIGTERM | Graceful lifespan shutdown: stop intake, drain in-flight jobs (30 s), close pools |
| Slow dependency | Timeouts everywhere (connect 5 s, read 10 s, upload 60 s) | No call waits forever; bulkheads keep interactive traffic flowing |
| Webhook misconfigured or cert expired | `getWebhookInfo` probe every 5 min (`last_error_message`, `pending_update_count`) | Alert; the runbook switches to the polling transport temporarily |
| Data loss | Nightly `pg_dump` to object storage with 14-day retention | Monthly restore drill into a scratch DB |

**Admin mistakes are a failure mode too.** A bad flow can't be published (the validator blocks it), it can be tried first in `/preview`, and if it still misbehaves, rollback to the previous version takes effect in every process within a second. Students whose session points to a screen that no longer exists land on the home screen.

**Observability.** Logs are JSON with `update_id`, `user_id` (hashed) and `trace_id` on every line. Prometheus metrics cover webhook latency, queue depth, Bot API calls by method and status, 429 count, cache hit ratio and DB pool usage. Liveness is `/healthz`; readiness is `/readyz`, which checks DB, Redis and breaker state.

## Execution phases

The plan has eleven phases and runs roughly 16 weeks at 10 to 15 hours a week (an estimate). The flow engine and admin panel add about four weeks to the earlier estimate. The reusable packages come first, so the bot is built on them rather than having them extracted later. Each phase ends with a gate: its done criteria pass in CI before the next phase starts.

&#91;embedded content: phase roadmap · 11 phases over 16 weeks\]

1. **Phase 0: Foundations (week 1).**
   - uv workspace with four packages; `ruff`, `mypy --strict`, `pytest`, pre-commit (`ruff`, `mypy`, `gitleaks`); a `Makefile` (`make lint test up`).
   - `docker-compose.dev.yml` with Postgres and Redis; `.env.example`; a GitHub Actions CI pipeline (lint, type-check, test, `pip-audit`).
   - `CLAUDE.md`, `.claude/settings.json` permissions, an ADR folder (`docs/adr/0001-hexagonal.md`, `0002-flow-as-data.md`).
   - **Done:** CI is green on an empty skeleton; `make up` starts the dev stack.
2. **Phase 1: `tg_gateway` (weeks 2 and 3).**
   - `BotClient` with `get_me`, `send_message`, `edit_message_text`, `send_document`, `answer_callback_query`, `set_webhook`, `get_webhook_info`, `delete_webhook`, `get_updates`, `set_my_commands`.
   - Pydantic types, error mapping, retry policy, rate limiters, callback codec, keyboard builder, router with filters and middleware, polling runner and webhook router factory.
   - **Done:** a 95% coverage target on the package; an echo bot works in polling mode; no imports from other packages except `platform_kit`. Enforce that with `import-linter` contracts.
3. **Phase 2: `platform_kit` (week 4).**
   - Config, logging with redaction, resilience decorators, idempotency store, Redis token bucket, feature flags, health and metrics, DB/UoW base, security helpers (HMAC, CSRF, sessions).
   - **Done:** unit tests for each part; property tests for the HMAC codec and redaction.
4. **Phase 3: `tg_flow` engine (weeks 5 and 6).**
   - Flow schema (screens, buttons, actions, commands, rules), validator, rule evaluator, safe placeholder renderer, runtime interpreter, registries, in-memory `FlowStore`, version cache with pub/sub invalidation.
   - **Done:** a demo bot driven only by a JSON flow file works in polling mode; property tests show random flows never crash the validator and rendered screens always respect Telegram limits.
5. **Phase 4: Domain and application (week 7).**
   - Entities and value objects, ports, use cases (`BrowseCategory`, `ListResources`, `SendResource`, `SearchResources`, `RegisterUser`, `IngestPdf`, `PublishResource`, `PublishFlow`, `RollbackFlow`, `StartBroadcast`, `ManageCategory`), role checks, in-memory fakes.
   - **Done:** every use case is tested against fakes with no I/O; the authorization matrix (role by use case) passes; the Arabic normalizer has property tests.
6. **Phase 5: Adapters (week 8).**
   - SQLAlchemy models and repositories, Postgres `FlowStore`, Alembic migrations, Redis cache and sessions, local and S3 storage, `TelegramMessenger`, seed scripts (default curriculum tree, default flow).
   - **Done:** integration tests pass against real Postgres and Redis (Testcontainers); migrations upgrade and downgrade cleanly; seeds are idempotent.
7. **Phase 6: Student experience (week 9).**
   - Catalog-browser and resource-list screen types, actions (send, search, favorite, subscribe, report, my data, delete me), the default flow in Arabic and English, deep links.
   - **Done:** component tests drive the full default flow with a mocked Bot API; manual run with the staging bot.
8. **Phase 7: Admin panel (weeks 10 to 12).**
   - Week 10: Telegram Login, sessions, CSRF, roles, layout; Curriculum and Files pages with the validation pipeline.
   - Week 11: Visibility and scheduling, Broadcasts, Students, Reports, Admins and audit, Dashboard.
   - Week 12: Bot flow editor (screens, buttons, commands, flags), validator feedback, simulator, draft/publish/rollback with a diff, `/preview` in the bot, Messages page.
   - **Done:** Playwright tests cover login, upload-to-publish, edit-flow-to-publish-to-student-sees-it and rollback; the route-guard test passes.
9. **Phase 8: Hardening (week 13).**
   - Security review against the OWASP API Top 10 and the panel checklist, bandit/Trivy/ZAP (the panel included), load tests against the targets, resilience tests with Toxiproxy, a review of dashboards and alerts.
   - **Done:** performance targets met; no high or critical findings open.
10. **Phase 9: Deployment (week 14).**
    - Free VM provisioned, Compose stack with Caddy, Telegram Login domain set in @BotFather, CI/CD deploy, backups and the restore drill, uptime alerting.
    - **Done:** the bot and panel work on the public domain; a restore drill succeeds; a zero-downtime redeploy is shown.
11. **Phase 10: Beta (weeks 15 and 16).**
    - Admins load the curriculum and content through the panel; 20 to 50 real students across several grades; collect reports; fix.
    - **Done:** a week with no P1 bugs; admins can run the bot with no developer help.

## Testing strategy

The test suite is a pyramid. Most tests are fast unit tests against fakes. Real Postgres and Redis appear only in integration tests, and the real Telegram only in the staging smoke test. Each layer gets a pytest marker so you can run it alone.

| Layer | What it proves | Tools | Marker / command | Runs |
| --- | --- | --- | --- | --- |
| Unit | Domain rules, use cases, normalizer, codec, limiter math | `pytest`, `pytest-asyncio`, `hypothesis`, in-memory fakes | `uv run pytest -m unit` | Every commit (under 10 s) |
| Component | Gateway behavior against a fake Bot API: retries, 429 handling, error mapping; webhook endpoint auth and dedupe | `respx` (mocks httpx), `httpx.AsyncClient(transport=ASGITransport(app))` | `uv run pytest -m component` | Every commit |
| Contract | Our Pydantic models accept real Telegram JSON | Recorded update fixtures in `tests/fixtures/telegram/*.json` | `uv run pytest -m contract` | Every commit |
| Integration | Repositories, migrations, Redis scripts, S3 adapter | `testcontainers` (Postgres, Redis, MinIO), `alembic upgrade/downgrade` | `uv run pytest -m integration` | Every PR (needs Docker) |
| End-to-end (staging) | Real Telegram: `/start`, browse, receive a PDF | A separate staging bot from @BotFather, plus a smoke script that drives it with a test account | `make e2e-staging` | Before each production deploy |
| Security | No known-vulnerable dependencies, no secrets, no unsafe code patterns; authorization matrix; webhook rejects bad tokens; admin API fuzzing | `pip-audit`, `bandit`, `gitleaks`, `trivy`, `schemathesis`, OWASP ZAP baseline; pytest authz matrix | `make security` | Every PR; ZAP weekly |
| Performance | Meets the targets in Performance | `locust` posting synthetic updates to the webhook, with Telegram replaced by a stub server | `make load` (for example `locust -u 500 -r 50 -t 5m`) | Before release; after big changes |
| Resilience | Behavior when Redis, Postgres or Telegram are slow or down | `toxiproxy` between app and dependencies; stub server returning 429/5xx | `uv run pytest -m chaos` | Nightly |
| Mutation (optional) | Tests actually catch bugs in core logic | `mutmut` on `domain/` and `application/` | `make mutate` | Weekly |

Tests added for the configurable flow and the admin panel:

| Layer | What it proves | Tools | Marker / command |
| --- | --- | --- | --- |
| Flow unit + property | The validator catches every broken-flow class; random valid flows render within Telegram limits; rules evaluate correctly | `pytest`, `hypothesis` strategies that generate flow documents | `uv run pytest -m flow` |
| Flow golden tests | The default flow renders each screen exactly as approved (text + keyboard snapshot) | Snapshot files in `tests/golden/` | `uv run pytest -m golden` (`--update-golden` after an intended change) |
| Panel component | Every route has a role guard; CSRF is required on every POST; login rejects a bad hash or stale `auth_date` | `httpx` + `ASGITransport`, route enumeration test | `uv run pytest -m panel` |
| Panel end-to-end | Login, upload to publish, edit flow to publish, the student sees the change in the simulated bot, rollback | Playwright for Python against the local stack with a stubbed Telegram | `make e2e-panel` |
| Panel security | XSS in titles and texts is escaped in both the panel and Telegram; no inline scripts under CSP | ZAP baseline on `/admin`, injection fixtures | `make security` |

**Coverage gates** (CI fails below them): 90% on `domain` + `application`, 95% on `tg_gateway`, 80% overall. Coverage is a floor, not a goal; review asks "what behavior is untested".

**Example tests worth writing first**

- `test_webhook_rejects_missing_or_wrong_secret` returns 401 and enqueues nothing.
- `test_duplicate_update_id_is_acked_once` makes the second POST return 200 with zero handler calls.
- `test_429_waits_retry_after_then_succeeds` uses respx to return 429 with `retry_after=2`, then 200, and asserts the sleep happened with a fake clock.
- `test_tampered_callback_data_rejected`, a hypothesis test that every single-byte mutation fails verification.
- `test_editor_cannot_manage_admins` is generated from the role-by-action authorization matrix.
- `test_stale_file_id_triggers_reupload_once`.
- `test_arabic_normalizer_idempotent`: `normalize(normalize(x)) == normalize(x)` for any text.

**Running everything locally**

```
make up            # docker compose: postgres, redis, minio, toxiproxy
make test          # unit + component + contract
make test-all      # + integration + chaos (needs Docker)
make security      # pip-audit, bandit, gitleaks, trivy
make load          # locust against local stack with Telegram stub
make run-polling   # run the bot locally against your dev bot token
```

## Deployment plan (free)

Deploy to an Oracle Cloud Always Free ARM VM running Docker Compose (app, worker, Postgres, Redis, Caddy). It is the only free option here that is always on, has persistent disk and can run the whole stack on one machine. The serverless free tiers below all sleep, and a sleeping bot means slow first replies.

**Free options compared** (checked November 2026)

| Option | What you get free | Catch | Verdict |
| --- | --- | --- | --- |
| [Oracle Cloud Always Free](https://terminalbytes.com/oracle-cloud-free-tier-changes-2026) | Ampere A1 ARM, now 2 OCPU / 12 GB total (halved from 4 / 24 GB; enforced from 18 Aug 2026 per [labmemo](https://labmemo.com/?p=55273)), plus AMD micro VMs and 200 GB block storage | ARM capacity is often "out of capacity" in popular regions ([space-node](https://space-node.net/blog/oracle-cloud-always-free-limits-2026)); card needed at signup | **Primary.** Always on, plenty for this stack |
| [Render free web service](https://render.com/docs/free) + [Neon Free Postgres](https://neon.com/faqs/free-plan-limits-and-quotas) | Web service with 750 instance hours a month; Neon gives 1 GB storage and 100 CU-hours per project | Render sleeps after 15 min idle and takes about a minute to wake ([Render](https://render.com/articles/platforms-with-a-real-free-tier-for-developers-in-2026)); ephemeral disk; Render's own free Postgres expires after 30 days | **Fallback.** Fine for demos; Telegram retries the webhook while it wakes |
| [Koyeb free instance](https://www.koyeb.com/docs/run-and-scale/scale-to-zero) | One small service | Scales to zero after 1 h idle (can't be disabled); a $29 card hold is now required ([SnapDeploy](https://snapdeploy.dev/koyeb-alternative)) | Not recommended |

**Primary deployment, step by step**

1. **Account and VM.** Sign up for OCI and choose a home region close to you or your users that has A1 capacity. Create `VM.Standard.A1.Flex` with 2 OCPU / 12 GB and Ubuntu 24.04 (arm64). If A1 capacity is unavailable, use the AMD micro VM (1 GB RAM) with Postgres tuned small. Attach a reserved public IP.
2. **Network.** In the VCN security list, open TCP 443 (and 80 for ACME) to everyone and 22 only from your IP. Mirror this with `ufw`. Telegram webhooks must use HTTPS on port 443, 80, 88 or 8443.
3. **Harden the host.** Create a non-root user with SSH keys, set `PasswordAuthentication no` and `PermitRootLogin no`, and install `unattended-upgrades`, `fail2ban`, Docker Engine and the Compose plugin.
4. **Domain and TLS.** Get a free subdomain (for example DuckDNS) pointed at the IP. Caddy obtains and renews Let's Encrypt certificates automatically.
5. **Images.** GitHub Actions builds a multi-arch image (`linux/arm64,linux/amd64`) with `docker buildx`, scans it with Trivy, and pushes it to GHCR tagged with the git SHA.
6. **Stack.** `deploy/compose.prod.yml` defines `caddy`, `api` (uvicorn, 2 workers), `worker` (arq), `postgres` (volume), `redis` (password, AOF on), and an internal network. Only Caddy publishes ports. Secrets go in `/opt/bot/.env` with mode 600, owned by the deploy user.
7. **First deploy.** `docker compose pull && docker compose run --rm api alembic upgrade head && docker compose up -d`, then `make set-webhook`, which calls `setWebhook` with the URL, `secret_token`, `allowed_updates` and `drop_pending_updates`. Verify with `getWebhookInfo`.
8. **CI/CD.** On a merge to `main` after all gates pass, a deploy job connects over SSH with a deploy-only key, pulls the new SHA, runs migrations, runs `up -d` and polls `/readyz`. Rollback means redeploying the previous SHA.
9. **Backups.** A nightly cron runs `pg_dump | gzip` and uploads to OCI Object Storage (part of Always Free) with 14-day retention. A monthly restore drill follows a script in `deploy/runbooks/restore.md`.
10. **Monitoring.** A free external uptime check on `/healthz`, a cron job that alerts you on Telegram (through the bot, to the owner) if `getWebhookInfo` shows errors or a growing `pending_update_count`, and log rotation via Docker `json-file` max-size.

**Admin panel in production.** The panel is served at `https://<your-domain>/admin` by the same `api` container. Set the domain for the Telegram Login Widget with `/setdomain` in @BotFather (one domain per bot). Caddy can add an IP allowlist for `/admin/*` and sets the security headers. Panel uploads stream directly to object storage, so the VM's disk never holds large files.

**Environments.** `dev` uses polling on your laptop with a dev bot. `staging` and `prod` each have their own bot token, database and webhook secret; staging can be a second Compose project on the same VM.

## Claude Code learning track

You learn each Claude Code skill in the phase where it pays off, by using it on this project. The core habit a company will expect is: plan first, keep Claude's context small and explicit, make it prove its work with tests you can run, and review every diff as if a colleague wrote it.

| Phase | Claude Code skill | Exercise in this project | Why companies care |
| --- | --- | --- | --- |
| 0 | `CLAUDE.md` project memory: `/init`, then `/memory` to refine | Run `/init`, then trim the result to facts only: commands (`make test`), layout, the dependency rule ("domain imports nothing from adapters"), and style rules | Teams share one CLAUDE.md per repo; it [loads every session](https://code.claude.com/docs/en/features-overview), so keep it short |
| 0 | Permissions and modes: `/permissions`, `.claude/settings.json` | Allow `make test`, `uv run *` and `git diff`; deny reading `.env*` and `deploy/secrets/*`; deny `git push` | Least privilege for an agent; [modes](https://code.claude.com/docs/en/glossary) include default, acceptEdits, plan and auto |
| 1 | Plan mode (`/plan`) and spec-first prompting | Before writing `BotClient`, have Claude propose the interface and test list; edit the plan, then approve it | Stops large wrong changes early; this is how you scope tickets |
| 1 | Test-first loop | You write (or approve) failing tests for 429 handling, then ask Claude to make them pass without changing the tests | Claude never grades its own work; the tests do |
| 1 to 3 | Context hygiene: `/clear` between tasks, `/compact` on long ones, point at exact files with `@path` | One task per session: "implement `callback.py` against `tests/unit/test_callback.py`" | Cheaper, more accurate sessions on large company codebases |
| 2 | Write a **skill** (`.claude/skills/<name>/SKILL.md`) | `new-use-case` skill: scaffolds the use case, its port, a fake and a test file in your house style, invoked as `/new-use-case` | [Skills](https://code.claude.com/docs/en/skills) are how teams package repeatable procedures |
| 3 | Custom **subagents** (`.claude/agents/*.md`) | A read-only `security-reviewer` (tools: Read, Grep, Glob) with the Security section as its checklist; a `test-gap-finder` | [Subagents](https://code.claude.com/docs/en/sub-agents) keep review isolated from the coding context |
| 4 | **Hooks** in `settings.json` | A PostToolUse hook runs `ruff format` + `ruff check` on edited files; a PreToolUse hook blocks writes to `migrations/versions/*` unless the prompt says "migration" | [Hooks](https://code.claude.com/docs/en/hooks) enforce rules deterministically instead of hoping the model complies |
| 5 | **MCP** servers (`/mcp`) | Connect GitHub (issues/PRs) and a read-only Postgres server for schema questions on the dev DB | Real company work spans tools, not just files |
| 6 | Parallel work with git worktrees | Run admin-API and broadcast-worker features in two worktrees with two sessions, then merge | Delivering more without the sessions colliding |
| 7 | Headless and CI: `claude -p`, `--bare` | A CI job asks Claude to review the PR diff against the Security checklist and post findings; `--bare` [makes CI runs reproducible](https://code.claude.com/docs/en/glossary) | Automated review gates are common in companies using Claude |
| 8 | Ops prompts with guardrails | Have Claude draft the runbooks and Compose file; you run every command on the server yourself | Agents propose; humans execute irreversible actions |

**Working rules to keep throughout**

- Every prompt states the goal, the files in scope, the acceptance criteria (which tests must pass) and what must not change.
- Read every diff before accepting it. Ask "why" for anything you would not have written, and reject cleverness you can't explain.
- Commit small, after green tests, with your own review. Never let Claude push to `main`.
- Never paste secrets into a prompt; the deny rules back this up.
- When Claude states a fact about a library or API, check it in the docs or with a test before relying on it.

## Risks and open questions

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Content rights: PDFs from publishers or teachers | Takedowns, bot ban | Upload only material you have rights to; record source and license per resource; keep a takedown process |
| Oracle free capacity or terms change again (the ARM allocation was already halved in 2026) | Downtime or forced migration | Everything is in Compose and backed up, so moving to any VPS takes about an hour; Render + Neon is the documented fallback |
| `file_id`s only work for the bot that obtained them | A second bot must re-upload | `telegram_files` is keyed by `bot_key`, and uploads are re-done lazily from object storage |
| Exam-season spikes | Slow replies | `file_id` sends, menu cache, load test at 2 to 3 times the expected peak, workers scale out |
| Arabic search quality | Students can't find files | Normalizer, trigram fuzziness, synonyms table (for example common subject nicknames), logging of zero-result queries |
| Bot token leak | Full takeover | Redaction, secret scanning, a rotation runbook rehearsed once |
| Single VM | One host is a single point of failure | Acceptable for the free test phase; next step is managed Postgres and two app nodes |

**Open questions for you**

- [ ] Who are the admins, and which role should each have (owner, publisher, editor, viewer)?
- [ ] Please confirm the default curriculum seed in the Admin panel section (stages, the 11th/12th scientific and literary split, any vocational or Sharia branches), and send a subjects list per grade if you have one. Are the files already organized for bulk import?
- [ ] Is the bot free or gated? Answered: completely free, no gating.
- [ ] Do students reach Telegram reliably on their connections, or do some need lighter flows such as smaller files and fewer messages?
- [ ] Should exam samples be grouped by year and session (first or second round)?
