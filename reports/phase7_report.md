# Phase 7 Report — Operational metrics & the measured latency budget

**Status: COMPLETE (P7-0…P7-7).** Log: `reports/phase7_testrun.log` — **155/155 backend (exit 0)**; frontend **27/27**. Plan: `docs/phase7_plan.md`. Decisions: **D-037…D-040** in `docs/DECISIONS.md` (D-037/D-038 new; D-039/D-040 cover the latency cache and the honest scope of the budget claim).

## What was built

1. **P7-1 — `backend/app/ops/timeillustrator.py`** (`docs/compare`-note: none shipped in prior phases; this is the first shared timing library): `Span` (hierarchical, `total_ms`/`self_ms`, active-span visibility), `MetricStore` (bounded ring buffer + counters, thread-safe, `aggregate()` per-name p50/p95, `render_text`/`render_json` with depth caps). No module-level mutable state: state lives on store instances; the one process singleton lives explicitly in `app/ops/registry.py` with the single writable seam `swap_store()` (used by tests and nobody else).
2. **P7-2 — `backend/app/main.py`**: the existing request-timing middleware now also opens a `METHOD /path` span per request and files it into the registry store, plus `requests:<METHOD+path>` and `…:errors` counters. Additive: `X-Process-Time-Ms`, the log line, and the Phase 3 `get_request_metrics()` payload on `/api/health` are untouched (asserted by test).
3. **P7-3 — `backend/app/api/ops.py`**: `GET /api/ops/metrics?window=` (per-endpoint count/avg/p50/p95/max from the ring buffer + counters) and `GET /api/ops/health-probe` (store occupancy, active/crashed spans, slowest endpoints, in-process honesty note). Admin-only (RBAC: anonymous 401, viewer 403 — tested). Shallow-data discipline: no DB contents, no filesystem paths, no secrets — the payload is asserted free of such strings in tests.
4. **P7-4 — `frontend/src/pages/Settings.jsx`**: admin-gated **Live Ops** panel (per-endpoint latency table); non-admins and signed-out users never fire the request (tested). New `frontend/src/pages/Settings.test.jsx` (5 tests; 27/27 frontend green).
5. **P7-5 — the P3-anchored latency pass, by measurement**: `yield_service.explain_lot`'s SHAP path now caches one `TreeExplainer` per model object (`app/ml/shap_service.py`, attribute-keyed so it is GC'd with the model). `rootcause_service.compute_root_cause` now emits `rootcause:compute` spans (`ml:shap`, `db:fetch`, `db:equip_stats` children), and the per-candidate `count_documents` fan-out (23 round-trips on the profile) is prefetched in one bundle (`_prefetch_equipment_stats`, 4 round-trips).

## The measurement (the part the plan demanded be honest)

Profiled the live engine over the real Atlas cluster on `LOT_10000` (`cProfile`, 2 warm calls):

- **Before**: engine calls at **2.9 s…3.98 s** per lot (P3's 2.8 s p50 / 3.5 s p95 reproduced).
- **Profile says**: `ml:shap` was only **~16 ms/call once warm** — the budget's real cost was **~31 Mongo round-trips** (~60–85 ms each on this network): `_equipment_association` did 2 `count_documents` per candidate×5 candidates + `_fab_baselines` + timeline/maintenance counts.
- **After**: per-candidate fan-out collapsed into `_prefetch_equipment_stats` (session-local, one aggregation). Engine calls now measure **~0.44 s…1.5 s warm** (network-jitter-dominated; first call after a cold process ~1.2 s).
- **Correctness gate**: `_equipment_association` prefetch vs legacy paths produce **identical scores** (asserted by test, live-verified content-equal modulo per-call `utc_now()` stamps); top ranking unchanged (`RF Power`, `Maintenance Overdue`, `Pressure`…).

**What is honestly NOT claimed:** the 0.4–1.5 s is network-bound to the remote Atlas cluster; a same-DC deployment was not measured here. `get_request_metrics`' Phase 3 p95 and `/api/health` payload are unchanged by design. No persistent/alerting-side cache was added (D-040) — the store is process-local and documented as such in the UI panel and `health-probe` note.

## Suites

- Backend: **155 passed / 0 failed (exit 0)** — `reports/phase7_testrun.log`. New module: `tests/test_phase7_ops.py` (15 tests: span contracts, ring-buffer bounds, active-span visibility, depth-capped renders, middleware spans + error counters, Phase-3-health-parity, ops RBAC 401/403/200, shallow-data leak guard, rootcause span tree, prefetch≡legacy).
- Frontend: **27 passed / 27** (`npx vitest run`).

## Limitations

- Ops numbers are **in-process only** (reset on API restart); the UI and `health-probe` both say so. No external APM was added — that remains out of scope (P3/D-027 lineage).
- The latency improvement is measured on one lot (`LOT_10000`) with the seeded dataset, warm explainer, and a remote Atlas cluster from this machine; it is a measured on-this-env numbers claim, not a universal one. The multi-lot sweep (`evaluate_rootcause.py`) was not re-run; candidate content was instead verified equal live modulo timestamps.
- The store swap seam (`swap_store`) is test-only; no production code mutates the singleton except through span recording.

## Handoff to Phase 8 (per surviving plan notes)

Phase 8 is the triage workflow (alert → ack → resolve with audit trail), which P7 deliberately did not build: the ops surface is counts/rates/robustness, not a triage UI. P9 remains CI.
