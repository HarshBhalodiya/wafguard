# WafGuard AI — Phase 0 Audit

Date: 2026-09-30 · Method: full read of `backend/`, `frontend/src/`, `scripts/`, `docs/`, `data/`, plus a live test run and artifact inspection. Nothing was taken from the README on trust.

## Headline

The codebase is in **materially better shape than the upgrade brief assumes**: there is no wall of hardcoded numbers. Yields, risk scores, root-cause candidates, and recommendations are all computed from trained XGBoost models, DB queries, or weighted formulas that live in code. The genuine gaps are different ones:

1. **Thin synthetic dataset** — 950 lots, 7 parameters, one scalar reading-set per lot, only one *authored* fault story (RF drift on ETCH_007). No hidden ground truth file, no chamber/equipment degradation dynamics, no defect→cause linkage written into the generator.
2. **No causal ground truth to evaluate against** — the root-cause engine is unmeasurable today; `is_demo_incident` is 1 lot.
3. **The "real" leg (SECOM) is disconnected** from the product (no lot IDs; prediction-only).
4. **README overclaims** — Slack alerting, RBAC on most endpoints, and the "watsonx.ai" copilot do not exist as described (details below).
5. **Broken dev environment** — `.venv` pointed at a Python 3.13 that isn't installed on this machine; rebuilt on 3.14 (see Risks).

Everything else on the P0–P10 list is additive, not repair work.

---

## 1. Environment status (found broken, fixed during this audit)

| Item | Status |
|---|---|
| `backend/.venv` | **Broken** — created on another machine (`pyvenv.cfg` → `C:\Users\harah\...\Python313`, which doesn't exist here; `E:\Projects\...` path also confirms a different box). Recreated as Python 3.14.7 venv, `requirements.txt` installed cleanly. |
| Test baseline | **41/41 backend tests pass** in 134s (mongomock; full seed+train in-session). |
| Frontend tests | 17 tests exist (`*.test.jsx`, vitest). Not re-run yet this session. |
| MongoDB | `.env` points to a **remote Atlas cluster with credentials in plaintext**. Tests don't need it (mongomock). |
| SECOM data | Present at `data/secom/` (real UCI download). |
| WM-811K | **Absent** (`data/wm811k/` empty) — vision model trains on procedural synthetic maps. Correctly labeled. |
| Trained artifacts | Present in `backend/models/artifacts/` (3 joblib bundles + CNN `.pt` + metadata + `evaluation_report.json`). |
| Stray file | `frontend/vite.config.js.timestamp-*.mjs` — Vite temp artifact, safe to delete. |

---

## 2. Endpoints (all verified in `backend/app/api/` + `main.py`)

| Endpoint | File | Verdict |
|---|---|---|
| `GET /api/health` | main.py | **real** (DB ping + request metrics) |
| `POST /api/auth/login` | auth.py | **real** (bcrypt + JWT; 5 seeded demo users) |
| `GET /api/dashboard` | dashboard.py | **real** (aggregates; computes root-cause on demand for ≤50 at-risk lots, caches) |
| `GET /api/lots`, `/api/lots/{id}` | lots.py | **real** (regex search, filters, sort, paginate) |
| `POST /api/yield/predict` | yield_prediction.py | **real** (XGBoost regressor+classifier → blended risk formula) |
| `GET /api/lots/{id}/root-cause` | rootcause.py | **partly fake** — formula is real and configurable, but 2 of 6 signals are heuristics dressed as evidence: `WAFER_PATTERN_CANDIDATE_BOOST` is an authored lookup table (e.g. "Edge-Ring → RF Power 0.70") not learned from data; `_equipment_association` is a text-match on `anomaly_status` string. The generator never injects faults per-cause, so nothing validates the output. |
| `GET /api/lots/{id}/recommendations` | recommendations.py | **partly fake** — real per-candidate template rendering, but the templates are a hardcoded knowledge base (acceptable per spec, but it must become a data file with case counts to meet P4). No "9/12 similar cases recovered" statistics exist anywhere. |
| `GET /api/wafers?lot_id=`, `/api/wafers/{id}/map` | wafers.py | **real** (CNN + Grad-CAM; lazily classifies and persists) |
| `POST /api/defects/analyze` | defects.py | **real** (upload → grayscale → circular mask → Otsu → die grid → CNN; honest note attached) |
| `GET /api/equipment`, `/api/equipment/{id}` | equipment.py | **real** (maintenance + recent lots + peers) |
| `GET /api/risk/lots` | risk.py | **real** (DB query on backfilled risk) |
| `GET /api/similar/{lot_id}` | similarity.py | **real but shallow** — sklearn kNN on 7 process params over standardized vectors, fit **per request** (no index, no embeddings). Fine at 950 lots; needs the P2 upgrade. |
| `POST /api/what-if` | simulation.py | **real** — re-inference on snapshot with overrides. Missing: physical/recipe limits on overrides (you can set rf_power=700, inside band but arbitrary; no impossible-value guard), no uncertainty. Correctly never persists. |
| `GET /api/models`, `/api/model-performance` | models_info.py | **real** (model_versions collection) |
| `POST /api/data/import` | data_import.py | **real**, and the **only RBAC-gated write** in the app |
| `POST /api/lots/{id}/copilot` | copilot.py | **real with caveats** — retrieval-first evidence bundle is genuine; LLM call is **Anthropic API, not watsonx.ai** (README mislabels it). Deterministic fallback works with no key. |

**Not implemented anywhere (README/docs claim otherwise):** Slack webhook alerting (alerts are DB rows only), RBAC on read endpoints (everything is open except `/data/import`), watsonx.ai integration.

---

## 3. ML models (artifacts inspected, `backend/models/artifacts/`)

| Model | Type | Verdict | Notes |
|---|---|---|---|
| `lot_yield_regressor` v1.0.0 | XGBoost + RF baseline | **real** | Chronological 70/15/15 split, no leakage (shifted rolling history, own-run sensors only; `defect_probability` correctly excluded). Metrics: MAE ≈ 3.6%, R² ≈ 0.58. |
| `lot_yield_classifier` v1.0.0 | XGBoost + RF baseline | **real** | ROC-AUC 0.955 / PR-AUC 0.884 on test. `scale_pos_weight` for imbalance; **no probability calibration** (P1 gap). Baseline ≈ production — suspicious, suggests both models saturate the synthetic signal. |
| `secom_failure_classifier` v1.0.0 | XGBoost + RF baseline | **real** but **orphaned** | Trains on real UCI SECOM (590 features, 1567 rows, chronological split). Referenced by `/api/models` and a `/yield/predict` path no frontend page calls. Disconnected from lots/wafers — fine, it's a different fab. |
| `wafer_pattern_cnn` v1.0.0 | PyTorch CNN + Grad-CAM | **real**, trained on synthetic maps | 85.7% acc / 0.834 macro-F1 on synthetic test split. `Loc` class F1 = 0.15 (weak class). Dataset string honestly says "procedurally generated". **Not linked to the tabular generator's patterns** — `pattern_hint` in `wafers.csv` only picks a look-alike map from a pool, so a wafer map never certifies the fault that caused its lot's yield loss. |

**Training scripts** (`backend/scripts/`): all five run for real with fixed seeds. `train_yield_model.py` backfills predictions/alerts into Mongo (optional at train time — degrades gracefully). **No Optuna/CV/calibration** — Phase 1 scope. **No ground-truth fault labels** — generator injects faults but never writes *which* fault it injected per lot.

---

## 4. MongoDB collections (schema reality)

| Collection | Contents | Verdict |
|---|---|---|
| `lots` | 950 docs: lot_id, product, node, wafer_count, process_start, equipment, process_step, status, yield, predicted_yield, risk_probability, risk_level, process_parameters, is_demo_incident, data_source | **real**; no `Wafer→Step→Chamber` chain; `node` column is buggy (always "5nm", see §6) |
| `wafers` | 23,750 docs: wafer_id, lot_id, slot, yield_status, synthetic_true_pattern, wafer_map_pattern/confidence, die_grid | **real**; die_grid is a look-alike, not generated from the lot's own fault |
| `equipment` | 30 docs: equipment_id, tool_family, chamber, location, health_score, status, anomaly_status, recent_lots(empty) | **real but static** — `health_score`/`anomaly_status` are fixed at generation time; degradation-over-time isn't modeled |
| `process_runs` | 950 docs: one row per lot with 7 scalar params | **partly fake** — these are *parameters*, not runs; no per-run time series |
| `sensor_data` (CSV only, never seeded to Mongo) | 57,000 rows: 15 readings × 4 params × 950 lots | **real but minimal** — 15 samples @ 30s cadence; no vibration; no dropouts/stuck/sensor-drift fault modes; no chamber tag |
| `defects` | ~2,900 docs: type, severity, x/y, size | **partly fake** — type counts are weighted random, not cause-linked; x/y uniform, no edge/scratch spatial logic |
| `maintenance` | ~180 events | **real**; only ETCH_007 is contrained (no recent PM, to keep its drift story) |
| `predictions` | 950 docs w/ feature snapshots + risk breakdown | **real**; per-lot backfill from training |
| `root_causes`, `recommendations` | cached per-lot results | **real**; idempotent upserts |
| `alerts` | HIGH-risk lots | **real**; DB-only, no webhook |
| `model_versions` | 4 models | **real** |
| `users` | 5 demo users | **real** |

Indexes: `mongodb.ensure_indexes()` covers all collections (single-field, ASCENDING). Fine for now; P1 adds compound indexes.

---

## 5. Frontend routes (14 pages, `frontend/src/App.jsx`)

| Route | Page | Verdict |
|---|---|---|
| `/dashboard` | Dashboard | **real** — live `/api/dashboard` |
| `/lots`, `/lots/:lotId` | Lots, LotDetails | **real** — live lot table + investigation tabs |
| `/yield-analytics` | YieldAnalytics | **real** — client-side aggregation over `/api/lots?limit=2000` |
| `/root-cause` | RootCause | **real** (inherits root-cause engine's partly-fake signals) |
| `/defect-analyzer` | DefectAnalyzer | **real** — image upload → CNN |
| `/wafer-map` | WaferMapPage | **real** — die grid + Grad-CAM overlay |
| `/risk-monitor` | RiskMonitor | **real** |
| `/equipment` | Equipment | **real** |
| `/recommendations` | Recommendations | **real** (template-based) |
| `/similar-lots` | SimilarLots | **real** |
| `/what-if` | WhatIf | **real** — sliders → `/api/what-if` |
| `/model-performance` | ModelPerformance | **real** — reads `model_versions` |
| `/settings` | Settings | **real** — risk-weight sliders stored in localStorage (frontend-only; weights on server stay in `config.py`) |
| `/login` | Login | **real** — JWT |

Components (26) are real, including `DataProvenanceBadge`, `EvidenceChain`, `RiskBadge`. 17 vitest tests exist and pass per README (not re-run this session). No loading-skeleton audit yet; error states exist in `useApi` hook.

---

## 6. Concrete bugs / smells found

1. **`generate_synthetic_data.py` node bug:** `"node": "5nm" if "5NM" in PRODUCTS[0] else "N/A"` — always `"5nm"` regardless of the lot's actual product. Harmless but wrong.
2. **`_wafer_mask` return arity:** docstring says 4 values, returns 5 (`yy, xx, dist, radius, center`); all callers unpack 5. Cosmetic.
3. **SENSOR_TRACKED_PARAMS omits `vibration`** (and etch/voltage/current time series) — Phase 1B adds them.
4. **No `chamber` linkage** on lots/process_runs — equipment docs have a chamber string but nothing joins lot→wafer→step→chamber→equipment as a first-class chain.
5. **`README.md` overclaims** (Slack, watsonx.ai, RBAC breadth) — must be corrected during Phase 1 doc updates.
6. **`.env` contains a live Atlas URI + credentials committed in plaintext.** Flagging; not touching credentials without instruction.
7. **`conftest.py` trains all models in-session** — the 41-test suite takes ~2.5 min; will need trimming as the suite grows (cache bundles to disk in CI later).
8. **`is_low_yield` derived from `yield` with threshold 85** — fine, but the generator's *true* causal fault label is never stored per lot. This is the single biggest blocker for P2 (root-cause evaluation) and it's a data-model change, so Phase 1 must add it.
9. **Similarity fits StandardScaler+NN per request** — O(n) refit on every call; fine at 950 lots, needs an index at 5,000+ (P2 plans FAISS/kNN store).
10. **Vision model**: `Loc` F1 0.15; class imbalance in real WM-811K will be much worse than the balanced synthetic set. P2's per-class recall reporting is already set up for this.

---

## 7. What is genuinely solid (do not break)

- Temporal split discipline (`chronological_split`, shifted rolling features, leakage-conscious `preprocessing.py` docstring explains in-situ sensor usage).
- Response envelope `{success, data, error, timestamp}` + typed exception handlers — consistent across all 16 routers.
- Repository pattern over Mongo; no raw pymongo in routes.
- Terminology discipline: "candidates, not confirmed causes" throughout root-cause/copilot code and tests.
- Honest synthetic-data labels (`DataProvenanceBadge`, `dataset_schema.py` provenance strings, dataset fields on every model artifact).
- Deterministic LLM fallback (copilot works with zero credentials).
- Tests assert *specific* behavior (incident lot ranks RF Power first, what-if improves yield and never persists) rather than "didn't crash".

---

## 8. Phase 1 plan (P0 — Foundation)

**Scope:** new data model + generator v2 + event timeline. No endpoint removals; existing pages keep working.

1. **`backend/app/data_model/`** — Pydantic v2 schemas: `Lot`, `Wafer`, `Equipment`, `Chamber`, `Recipe`, `ProcessStep`/`ProcessParameter`, `SensorTelemetry`, `Defect`, `WaferMap`, `MaintenanceEvent`, `ElectricalTestResult`, `TimelineEvent`. Full chain `Lot → Wafer → Step → Chamber → Equipment` via stable IDs (`lot_id`, `wafer_id`, `step_id`, `chamber_id`, `equipment_id`).
2. **`backend/app/simulator/`** — generator v2 (new module; old script kept working):
   - ≥5,000 lots, 30+ equipment, chambers per tool, ≥20 recipes.
   - Per-run time series @1 Hz-ish fixed cadence: pressure, temperature, RF power, gas flow, vibration, +2 more (≥5 total), with **fault modes**: pressure instability, RF drift, sensor slow bias, equipment degradation (worse with days-since-maintenance, reset by PM), sensor dropouts/stuck, recipe-transition problems, contamination events.
   - **Causal chain in the generator**: fault → mechanism (e.g. particles) → spatial wafer pattern → defect density → yield loss. This is what makes P2's evidence graph testable.
   - **`data/synthetic/ground_truth.parquet`** — per-lot true fault label(s), onset time, mechanism. Written separately; **never read by training code** (enforced by a test).
   - Config file `backend/app/simulator/config.yaml` with failure rate (default ~15%), seeds, rates. Same command regenerates everything.
   - Electrical test results per wafer (params correlated with the fault).
3. **Event timeline** — ordered event stream per lot/equipment: process start/end, parameter excursions (with onset + magnitude), equipment alarms, maintenance, recipe transitions, inspections. New `timeline_events` collection + `/api/lots/{id}/timeline` + `/api/equipment/{id}/timeline`.
4. **Seed/migrate script** — `scripts/seed_database.py` extended (old collections untouched; new collections added). Indexes: compound `(lot_id, ts)`, `(equipment_id, ts)`, `(parameter, ts)`.
5. **Tests** — schema validity for every collection; no duplicate IDs across lots/wafers/wafers-per-lot; value ranges sane; failure rate within config tolerance; **ground-truth ⇄ injected-fault consistency** (e.g. every lot labeled `rf_drift` shows positive RF slope, controls don't); **no-training-code-reads-ground-truth** guard (import-linter-style test); timeline ordering monotonic; one-command `python scripts/build_dataset.py` runs generate→seed→train→evaluate.
6. **Docs** — `docs/AUDIT.md` (this file), `docs/DECISIONS.md`, update README synthetic-data section honestly. `reports/phase1/` with generator stats (lot count, fault-rate table, sample telemetry plot).
7. **Old data** — keep the 950-lot CSVs generated as `data/synthetic_v1/` fallback? **Decision:** no — keep old files where they are, generate v2 into the same `data/synthetic/` paths with a `_schema_version: 2` marker, and let re-running the one-command rebuild restore everything. Old endpoints keep working because the old collections are re-seeded from the new CSVs' back-compat views.

**Risks for Phase 1:**
- **Size**: 5,000 lots × 25 wafers × full sensor series in Mongo ≈ hundreds of MB if die_grids are stored naively; mitigation: store die grids compressed (run-length or PNG bytes) and keep raw telemetry in parquet with Mongo holding downsampled/summary series.
- **Runtime**: richer generator + 5k lots could push seeding past a few minutes; mitigation: vectorize generation, keep default failure rate config, measure and report.
- **Back-compat**: existing tests assert `total_lots == 950` and `len(equipment) == 30`; those assertions must be updated to read counts from the generator config, not hardcode them.
- **Ground-truth leakage**: easiest way to silently ruin every later phase — hence the guard test from day one.

Stopping here per the phase protocol. Awaiting "continue" to start Phase 1.
