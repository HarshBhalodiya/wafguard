# Phase 1 (P0) Report — Foundation: data model, generator v2, event timeline

Date: 2026-10-01 · Status: complete · All numbers below are measured, not estimated.

## What was built

**1A. Manufacturing data model** — [backend/app/data_model/entities.py](../backend/app/data_model/entities.py)

Typed Pydantic v2 schemas for Lot, Wafer, Equipment, Chamber, Recipe, ProcessRun,
SensorTelemetry, Defect, WaferMap, MaintenanceEvent, ElectricalTestResult,
TimelineEvent, plus GroundTruth — the full Lot → Wafer → Step → Chamber → Equipment
chain. Every entity carries `is_simulated` / `data_source` provenance. Indexes for
the new collections were added in `app/database/mongodb.py::ensure_indexes`.

**1B. Simulator v2** — `backend/app/simulator/` (`config.json` + typed loader, `process_physics.py`, `mechanisms.py`, `generator.py`, `writer.py`)

- 5,000 lots (config-driven), 40 tools across 6 families, 78 chambers, 24 recipes,
  25 wafers/lot, 6 sensor channels at 12 s sample rate over 30 min runs
  (pressure, temperature, RF power, gas flow, vibration, plasma intensity).
- 7 injected fault modes, each wired to a named causal mechanism
  (`app/simulator/mechanisms.py`): fault → sensor signature + particle generation →
  spatial wafer pattern → defect density → yield loss → electrical pass rate.
  The link lives in code, so Phase 2+ evidence graphs can be *evaluated* against it.
- Hidden ground truth per lot (true fault, onset sample, mechanism, spatial signature)
  written to `data/synthetic/ground_truth.parquet`. A test
  (`tests/test_ground_truth_leakage.py`) enforces that no inference/training module
  may read it.
- Equipment degrades with days-since-PM; maintenance events reset it and are placed
  on the timeline. Telemetry includes dropouts, stuck values, and ambient excursions.
- One command: `python scripts/build_dataset.py`. Dataset fingerprint (`config_hash`)
  is written to `_generation_metadata.json` and used by the resumable seeder.

**1C. Event timeline** — generator emits 7 event types
(PROCESS_START/END, PARAMETER_EXCURSION, EQUIPMENT_ALARM, MAINTENANCE,
RECIPE_TRANSITION, INSPECTION) → `events.parquet` → Mongo `timeline_events` →
`GET /api/timeline/lot/{lot_id}` and `/api/timeline/equipment/{equipment_id}`
(`app/api/timeline.py`, tested).

**Storage decision (D-013):** wafer die grids are no longer stored in Mongo —
`app/services/wafer_map_store.py` materializes a deterministic grid per wafer from
(wafer_id, spatial_signature, defect_density). Same wafer always renders the same
map; faulty lots visibly show their mechanism's signature.

## How to run it

```bash
cd backend
python scripts/build_dataset.py        # 1. regenerate the 5,000-lot dataset
python scripts/seed_database.py        # 2. seed Mongo (resumable; safe to re-run)
python scripts/train_yield_model.py    # 3. train + backfill predictions/risk
python scripts/train_wafer_model.py    # 4. vision model (unchanged, procedural maps)
pytest -q                              # full test suite
```

## Test results

**56/56 pass** (41 pre-existing updated + 15 new). New suites:
`test_simulator.py` (schema validity, unique IDs, range checks, 7 fault mechanisms
present, ground truth ↔ injected-fault match via measurable signatures, drift lots
out-of-spec at run end), `test_ground_truth_leakage.py`, timeline API tests.
Test fixtures generate their own 1,500-lot dataset in-process (no disk dependency);
investigation tests anchor on a **test-split** faulty lot (D-012).

Two real bugs were found and fixed by these tests (see DECISIONS D-010/D-011):
sensor drift was invisible-in-telemetry physics, and the risk engine's
`parameter_deviation` signal was silently 0.0 for v2 snapshots.

## Dataset metrics (measured, seed=20260930)

| Quantity | Value |
|---|---|
| Lots / wafers / defects | 5,000 / 125,000 / 2,202,268 |
| Telemetry rows | 1,440,000 (6 channels × 150 samples × 5,000 lots) |
| Timeline events | 16,425 |
| Low-yield fraction | **0.115** (target band 0.10–0.20; generator aim 0.15) |
| Fault distribution | none 3,393 · equipment_degradation 352 · rf_power_drift 275 · chamber_pressure_instability 269 · sensor_drift 199 · contamination 183 · recipe_transition 177 · sensor_dropout 152 |
| Ground truth rows | 5,000 (100% of lots labeled) |

## Model metrics (held-out chronological test set, n=750)

| Model | Metric | Baseline (RF) | Production (XGBoost) |
|---|---|---|---|
| lot_yield_regressor | MAE | 2.99 | 3.12 |
| lot_yield_regressor | RMSE | 4.77 | 4.88 |
| lot_yield_regressor | R² | 0.407 | 0.378 |
| lot_yield_classifier | Precision | 0.60 | **0.75** |
| lot_yield_classifier | Recall | 0.60 | 0.58 |
| lot_yield_classifier | F1 | 0.60 | **0.65** |
| lot_yield_classifier | ROC-AUC | — | 0.80 |
| lot_yield_classifier | PR-AUC | — | 0.65 |

Baselines kept alongside production per the brief. The classifier beats the RF
baseline on F1/precision; the RF baseline is slightly better on regression — both
facts are reported, not hidden. SECOM (real data) remains near-useless on its
failure class at this sample size — it is real data, kept for contrast, and labeled as such.

These are **synthetic-data numbers**: they demonstrate pipeline correctness, not
real-world accuracy.

## Remote database (Atlas) state

User approved reseeding the shared Atlas cluster (M0 free tier, 512 MB). Outcome:

- All 10 base collections seeded: 5,000 lots, 125,000 wafers, **203,751 defect docs (capped at 50/lot — D-015)**, 168 maintenance, 5,000 electrical, 16,425 timeline events. Full 2.2M-row defect table stays in `data/synthetic/defects.csv` for ML.
- Trained models backfilled: 5,000 lots scored — risk levels LOW 4,365 / MEDIUM 371 / HIGH 264; 264 alerts raised; `predictions` has one snapshot per lot.
- Sanity check: a HIGH-risk lot (LOT_10038) shows actual 84.62 vs predicted 84.35 with 4 timeline events.
- The seeder is fingerprinted, resumable, and monotonic (D-014/D-015); flaky WAN drops and one quota trip were both recovered by re-running the same command.

## Known problems / things that could be wrong

1. **Synthetic causal links are by construction** — ground truth is trustworthy
   because the generator wrote it, not because the physics is realistic. Root-cause
   evaluation in Phase 3 measures *consistency with the generator's rules*.
2. **Regressor R² = 0.38 is modest** — much yield variance is mechanism noise +
   realized-defect stochasticity. Phase 2 feature engineering (temporal aggregates,
   equipment age) should lift it; the target list includes comparing stronger models.
3. **Atlas M0 quota caps the defect collection** — Mongo holds 50 defects/lot
   (203,751 docs); queries that need the full 2.2M rows must read `defects.csv`
   (D-015). Upgrading the cluster tier removes the cap.
6. **Event payloads carry null keys** for fields irrelevant to the event type
   (writer explodes payloads into fixed columns). Cosmetic; normalize in Phase 2.
4. **Test suite runtime grew to ~3 min** (it trains models twice in-session).
   Acceptable now; Phase 2 may split fast/slow test tiers.
5. `.env` still contains live Atlas credentials in plaintext (D-003) — rotate when convenient.

## Next phase (P1, per the brief)

Strong tabular pipeline: temporal aggregates (slope, excursion counts,
time-outside-limits), time-aware CV, Optuna search, calibration + reliability
plots, feature importance (gain + SHAP), baselines comparison table —
`reports/tabular_comparison.md`.
