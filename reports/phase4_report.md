# Phase 4 Report — Access control, alert delivery & integration honesty

> **SYNTHETIC DATA demo.** Nothing here claims production-hardening; `utils/security.py`
> says so in its own docstring. What Phase 4 delivers is *enforced, testable* access
> control, alerting that actually reaches a channel with a delivery record, and honest
> naming of the copilot's phrasing provider.

## What changed

| Was | Now |
|---|---|
| JWT + roles existed, but `require_roles` guarded exactly one endpoint; every read was anonymous | Every router except `/api/health` + `/api/auth/login` requires a valid JWT; an explicit per-action role matrix governs mutations (P4-1) |
| Alerts were rows written by the train backfill — nothing delivered them | `alert_dispatch_service`: `raise_alert()` creates + dispatches through pluggable channels (`log` default, generic `webhook` JSON POST, optional `smtp`); every attempt recorded in `alert_deliveries` (P4-2) |
| No way to ask "did anything receive this alert?" | `GET /api/alerts/deliveries`, admin channel CRUD at `/api/alerts/channels`, admin `POST /api/alerts/scan` (raise+dispatch for unalerted HIGH lots, deduped), admin `POST /api/alerts/{id}/redeliver` (P4-3) |
| Copilot LLM path hard-coded to Anthropic; response never named a provider | Provider adapters `anthropic` + `openai_compatible` (base-URL configurable); response carries `llm_provider` (null without a key) and `source: llm:<provider>` (P4-6/P4-7) |
| Nothing prevented re-committing a secret | `tests/test_secret_scan.py` scans every git-tracked file for credential-shaped strings and asserts `.env` stays untracked (P4-5) |

## RBAC matrix (as enforced, P4-1)

| Action | viewer | process_eng | yield_eng | equipment_eng | admin |
|---|---|---|---|---|---|
| All reads (`/lots`, `/dashboard`, `/alerts`, `/risk`, ...) | ✅ | ✅ | ✅ | ✅ | ✅ |
| `POST /api/what-if` | ❌ | ✅ | ✅ | ❌ | ✅ |
| `POST /api/lots/{id}/copilot` | ❌ | ✅ | ✅ | ✅ | ✅ |
| `POST /api/defects/analyze` | ❌ | ✅ | ❌ | ✅ | ✅ |
| `POST /api/data/import` | ❌ | ✅ | ❌ | ❌ | ✅ |
| Alert channels / scan / redeliver | ❌ | ❌ | ❌ | ❌ | ✅ |

Anonymous → 401 on everything except health/login; wrong role → 403
(`tests/test_rbac.py`, 12 tests incl. the full anonymous matrix and bad-credential login).

## Dispatcher evidence (P4-2, tests in `tests/test_alert_dispatch.py`)

- `raise_alert` writes the alert row (with the `message` field the UI renders) **and** a
  `log`-channel delivery record — the demo-safe default needs zero external credentials,
  mirroring the copilot's deterministic fallback.
- Webhook success records `sent` with the JSON payload (alert id, lot, severity, risk,
  reason, source, timestamp).
- Webhook outage: 3 attempts (1 try + 2 retries with backoff), then a `failed` delivery
  with the error string — and **no exception propagates** (the never-raise contract; a
  dead channel must not break training/backfill/API).
- Disabled channels are skipped without noise; the master switch
  `ALERT_DISPATCH_ENABLED=false` disables all delivery; unknown channel types record
  `skipped` instead of crashing.
- The train backfill now routes every HIGH-risk alert through `raise_alert` (source
  `backfill`); `POST /api/alerts/scan` dedupes per lot, so repeated scans create nothing.
- Channels are env-seeded (`ALERT_WEBHOOK_URL`, `ALERT_SMTP_*`) and admin-managed via the
  API; channel listings mask webhook targets so a listing never leaks a secret URL.

Deliberately **not** claimed: no vendor-specific "Slack integration" — the webhook
channel is generic JSON, which accepts Slack incoming webhooks among others. README says
exactly that (it previously said the opposite, over-claiming Slack in Phase 0).

## Copilot honesty contract (P4-7)

`answer_question` now returns `llm_provider`: `null` (deterministic template — the
default, works with zero credentials), `"anthropic"`, or `"openai_compatible"` (any
OpenAI-style `/chat/completions` endpoint via `LLM_BASE_URL` — self-hosted or vendor).
`source` becomes `llm:<provider>` when an LLM answered. No other vendor is named anywhere
in code, docs, or README; the Phase-0 mislabel of this as "watsonx.ai" is not repeated —
a watsonx-compatible endpoint would work through the openai_compatible adapter and would
be reported as exactly that.

## Verification (P4-9)

- Backend: `tests/test_rbac.py` 12/12, `tests/test_alert_dispatch.py` 6/6,
  `tests/test_secret_scan.py` 2/2, full suite green (see `reports/phase4_testrun.log`).
- Frontend: 20/20 vitest — unchanged this phase (the axios interceptor already attaches
  bearer tokens; the login flow already exists).
- Existing API test clients now authenticate as admin (the anonymous matrix lives in
  test_rbac.py); the data-import test's anonymous-401 check sends an explicit empty
  header so it still tests what its name says.
- One-command pipeline unaffected: it talks to Mongo directly, never through the API.

## Known limitations / follow-ups

- Channel-management UI is out of scope this phase (API-only); the dashboard still shows
  only the alert rows, not delivery status.
- Alert acknowledgement/resolution fields exist (`acknowledged_by`, `resolution`) but have
  no workflow yet — that is Phase 8's triage loop (P8-1), not silently half-built here.
- Swagger exploration now requires fetching a token from `/api/auth/login` first
  (documented in README).
- The RBAC gate is demo-grade by design (single shared JWT secret, no token refresh /
  revocation) — stated in `utils/security.py`, not hidden.
