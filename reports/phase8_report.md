# Phase 8 Report — Alert triage workflow & equipment-health card

Date: 2026-10-12 · Plan: `docs/phase8_plan.md` (P8-1…P8-6, all delivered) · Decisions: D-041…D-043 in `docs/DECISIONS.md`

Phase 8 closes the workflow Phase 4 deliberately left open: alerts could be raised and
delivered, but nothing ever acknowledged or resolved one. They can now — through a validated
state machine whose every transition lands in an append-only audit trail — and the
equipment-health view finally carries the SPC context the Phase 5 control charts were built for.

## What was built

| Step | Where | What |
|---|---|---|
| P8-1 | `app/services/alert_triage_service.py`, `alert_repository.py` | `open → acknowledged → resolved` state machine (illegal/duplicate transitions → typed 422; `resolved` is terminal; ack-before-resolve). Alert doc gains `status` / `acknowledged_by/at` / `resolved_by/at`; every transition appends exactly one event `{alert_id, from, to, actor{username,role}, at, note}` to a new **append-only** `alert_events` collection. Legacy rows (raised before Phase 8, incl. the live backfill) **derive `open`** — the shared Atlas was not rewritten; `status_clause()` expresses the same derivation as a Mongo query so the status filter also works without a backfill. |
| P8-2 | `app/api/alerts.py` | `POST /api/alerts/{id}/acknowledge`, `POST /api/alerts/{id}/resolve` (engineer roles + admin; viewer 403; anonymous 401), `GET /api/alerts/{id}/timeline` (any authenticated role), `status` filter on `GET /api/alerts` (unknown value → 422, not a silent empty list). All alert-by-id routes share one `_find_alert()` lookup. **Dispatcher untouched**: triage writes no delivery records and re-dispatches nothing (test-pinned). |
| P8-3 | `frontend/src/pages/Alerts.jsx` (+ route, sidebar entry) | `/alerts` triage page: status/severity filters, uppercase status badges, per-alert expansion showing the audit timeline + delivery records + lot link, note box, and acknowledge/resolve buttons rendered **only** for triage-capable roles (the backend matrix mirrored client-side). Dashboard's Recent Alerts panel gained a "View all & triage →" link; `AlertPanel` shows a status chip when the payload carries one (additive — the pinned Dashboard mock shape is unchanged). |
| P8-4 | `EquipmentStatusCard.jsx`, `Equipment.jsx` | Equipment-health cards show an "SPC (lot-level): N breaches · last 40 lots" chip from the existing P5-2 `GET /equipment/{id}/control-chart` API (rf_power channel, fetched after the list renders; a failed chart simply omits the chip). The opened equipment detail adds a per-parameter Control-Chart Context grid (4 parameters) labeled *"Lot-level SPC … not per-reading SPC"* (D-031). No new chart math; the generator's static `health_score` is rendered unchanged and not reinterpreted. |
| P8-5 | `tests/test_phase8_triage.py`, `pages/Alerts.test.jsx`, `pages/Equipment.test.jsx` | Transition matrix (legal + illegal + terminal), audit completeness (one event per transition, actor+timestamp), RBAC 401/403/200 per action, legacy-`open` derivation, status-filter validation, the D-030 no-recovery-claim guard (service-level + API 422), and the dispatcher-untouched contract. Frontend: role-gated actions, filters, expansion, failure-tolerant SPC chips. |
| P8-6 | this report, `docs/DECISIONS.md`, `README.md`, `docs/phase8_plan.md` | Docs and counts. |

## Verification

- **Backend: 169 passed / 0 failed (exit 0)** — `reports/phase8_backend_testrun.log`
  (155 prior + 14 new in `tests/test_phase8_triage.py`), run over all changed files.
- **Frontend: 36 passed / 36** (`npx vitest run`, 27 prior + 9 new) and a clean
  `npx vite build` (the >500 kB chunk warning is pre-existing).
- **Live smoke against the running API + shared Atlas (read-only):** admin login →
  `GET /api/alerts?status=open` returns the real backfill alerts with derived
  `status: open` and string `alert_id`s; `status=bogus` → 422; anonymous acknowledge → 401;
  unknown-alert timeline → 404. No live mutation was performed on the shared cluster —
  the ack/resolve paths are exercised hermetically by the suite against mongomock.

## Honest limits

- The audit trail is append-only Mongo rows, **not a tamper-proof ledger**; auth is still the
  demo-grade single-secret JWT gate (D-027). Both are stated in the report and the page copy.
- Triage changes nothing about dispatch: no resolve-time notification, no new channels, and
  the P4-2 never-raises contract is untouched (test-pinned).
- Resolution notes are free text and are **rejected** if they claim an outcome
  ("recovered…") — the generator records no interventions, so a recovery cannot be observed
  (D-030). The guard is a wording boundary, not a semantic proofreader.
- No `reopen` transition: `resolved` is terminal this phase; adding one is a deliberate
  future cut, not an oversight left half-built.
- The equipment card fires one small control-chart request per tool (30 on the seeded fab)
  after the list renders; the detail view fetches 4 parameters on selection. No backend
  batching endpoint was added — that would have been new surface beyond the to-do.
- Dashboard `recent_alerts` intentionally stays without a derived `status` field (the panel
  links to `/alerts` instead); annotating it would touch the pinned Phase-3 payload contract
  for no triage benefit.
