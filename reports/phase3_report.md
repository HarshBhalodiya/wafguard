# Phase 3 Report — Root-Cause Intelligence (P2)

> **SYNTHETIC DATA.** Every number below is measured on the simulated 5,000-lot
> dataset (generator seed 20260930) against its hidden labels. None of this is
> real-fab accuracy. Root-cause output remains **ranked candidates with evidence**
> — correlation, not proof of cause.

## What changed (vs the pre-upgrade engine)

| Was | Now |
|---|---|
| Authored `WAFER_PATTERN_CANDIDATE_BOOST` dict (e.g. Edge-Ring → RF 0.70) | Measured pattern→cause table built on the train split, with counts + smoothing + source citation (P3-1) |
| `anomaly_status` text-match (0.9/0.4/0.1 authored constants) | Measured excursion/failure/maintenance/low-yield rates from events+maintenance+lots, shrunk to the fab baseline for low-N tools, sample sizes in evidence (P3-2) |
| SHAP groups ignored Phase 2A extended columns | slope / excursions / time-outside join each candidate's SHAP group and appear as evidence (P3-3) |
| Temporal signal = last-minus-first drift | Per-channel shape: OLS ramp + end-drift + time-outside, described in the evidence label (P3-4) |
| Evidence had no ids; recommendations were orphans | Stable `evidence_id`s; every recommendation carries `grounded_in` (P3-6) |
| Parameter kNN only | + cosine search over temporal/vision embeddings at `/api/similarity/{lot_id}` (P3-5) |

## Evaluation method (P3-7)

- Engine: `rootcause_multi_signal_v2`, pattern table v1.0.0,
  shipped artifact matches fresh train-split build: **True**.
- Split: chronological test split (last 15% of lots by process_start). Scored **182** faulty lots
  (unmappable, excluded from metrics: {'contamination_event': 32, 'sensor_dropout': 26}).
- Fault → acceptable candidates: documented per fault in `app/evaluation/fault_mapping.py`;
  multi-mapped faults get fractional credit in the table builder.
- Baseline: rank by (shap_attribution + parameter_deviation) — the pre-upgrade core ranking.

## Results — new engine vs baseline

### Overall

| Engine | n | Top-1 | Top-3 | Top-5 | MRR |
|---|---|---|---|---|---|
| **new (multi-signal)** | 182 | 0.7088 | 0.8022 | 0.8407 | 0.7722 |
| baseline (SHAP+deviation) | 182 | 0.7198 | 0.7418 | 1.0 | 0.7804 |

### Per fault type

| Fault | n | Engine | Top-1 | Top-3 | Top-5 | MRR |
|---|---|---|---|---|---|---|
| chamber_pressure_instability | 32 | **new** | 0.9375 | 1.0 | 1.0 | 0.9688 |
|  |  | baseline | 1.0 | 1.0 | 1.0 | 1.0 |
| equipment_degradation | 51 | **new** | 0.0 | 0.2941 | 0.4314 | 0.2067 |
|  |  | baseline | 0.0 | 0.0784 | 1.0 | 0.2163 |
| recipe_transition_problem | 24 | **new** | 1.0 | 1.0 | 1.0 | 1.0 |
|  |  | baseline | 1.0 | 1.0 | 1.0 | 1.0 |
| rf_power_drift | 44 | **new** | 1.0 | 1.0 | 1.0 | 1.0 |
|  |  | baseline | 1.0 | 1.0 | 1.0 | 1.0 |
| sensor_drift | 31 | **new** | 1.0 | 1.0 | 1.0 | 1.0 |
|  |  | baseline | 1.0 | 1.0 | 1.0 | 1.0 |

## Engine latency (per call, in-memory DB)

- p50 **2807.5 ms**, p95 **3498.3 ms**, mean 2939.4 ms

## Honest failures / limitations

- `sensor_dropout` and `contamination_event` lots have **no defensible candidate** in the
  current 10-candidate set — they are counted above and excluded from the metrics rather
  than being scored leniently. The candidate set would need sensor-health / contamination
  candidates to cover them (candidate-set change, not done silently).
- The measured Edge-Ring association is genuinely ambiguous in the generator (both
  rf_power_drift and chamber_pressure_instability paint Edge-Ring) — the table shows the
  ambiguity instead of the old dict pretending to disambiguate.
- The pattern table is measured on the train split from SYNTHETIC labels; it stands in for
  historical failure-analysis records. It is not read by any training code
  (tests/test_ground_truth_leakage.py enforces this).

See `reports/phase3_rootcause_metrics.json` for full machine-readable metrics.

## Failure analysis: why `equipment_degradation` regresses (probe of 15 lots, same seeded DB)

Both engines score **Top-1 = 0.0** on `equipment_degradation` (n=51). The probe explains both
the Top-3 gain and the Top-5 loss:

1. **The tool-condition candidates are never dropped.** With the P3-6 evidence filter active,
   an acceptable candidate (Maintenance Overdue / Equipment Failure History / Equipment Yield
   History) was present in the ranking of **15/15** probe lots — the filter is not the cause.
2. **Measured equipment association genuinely fires.** Tool-condition candidates carry a
   strong measured `equipment_association` signal (1.0 after shrinkage) on these lots, lifting
   one of them to rank 3 in 4/15 probe lots — this is the Top-3 improvement (0.078 → 0.294).
3. **Signal stacking buries them in Top-5.** Degradation-era lots also show mild multi-channel
   parameter noise, so parameter candidates stack 3–4 weak-to-medium signals (SHAP 0.20–0.35,
   deviation, historical-occurrence up to 1.0, equipment 0.2–0.7) reaching scores ≈0.25–0.30,
   while tool-condition candidates carry one strong 0.15-weight signal (scores ≈0.15–0.18).
   Result: tool-condition candidates land at ranks 3 and 7–10 — inside baseline's top-5
   (1.0) but frequently outside the new engine's top-5 (0.43).
4. **Everything on these lots is LOW confidence (< 0.40).** The UI shows hedged candidates,
   not confident wrong answers — but that is mitigation, not a win.
5. **Top-1 stays 0.0 for both engines** because degradation has no dominant single-channel
   signature and the 10-candidate set has no vibration/tool-condition *parameter* candidate
   (see `app/evaluation/fault_mapping.py` for the defensible mapping).

**Concrete fix deferred, not done silently:** a max-signal term (a candidate with one strong
measured signal should not be buried under four weak ones) or a dedicated tool-condition
candidate. Both are weights/candidate-set changes that must be re-validated with this same
harness; tuning them against the test split here would be fitting the benchmark. Logged as
follow-up in D-025.

Also honest: the measured Edge-Ring ambiguity cost ~1 lot of Top-1 on
`chamber_pressure_instability` (0.9375 vs baseline 1.0) — the measured table puts Pressure at
0.50 and RF Power at 0.46, and the old authored dict would have hidden that ambiguity
without resolving it.

## Engine latency

p50 **2.8 s** / p95 **3.5 s** per lot — dominated by the per-call SHAP pass in
`yield_service.explain_lot`; the multi-space similarity search itself is milliseconds.
This is an API-interactive engine, not a batch scorer; a latency budget + caching belongs
to Phase 7 (P7-6) and is not claimed done here.

## Verification (P3-9)

- Backend suite: **90/90** pass (`pytest -q`, Python 3.14, mongomock). Frontend: **20/20** (vitest).
- The shipped artifact `models/artifacts/evidence/pattern_cause_associations.v1.json` has
  `associations` **identical to a fresh train-split build** (harness parity check: `True`).
- Leakage guard green: no `ground_truth` / `true_fault` string under `app/features`,
  `app/ml`, `app/services` or `scripts/train_*.py` — the measured table is consumed only as
  a versioned artifact.
- Similarity: old `/api/similar/{lot_id}` shape unchanged (test), new
  `/api/similarity/{lot_id}` returns all three spaces with per-space coverage; vision space
  honestly reports its 810/5,000-lot coverage.
- Recommendations: every `recommendation_items[].grounded_in` references real evidence ids
  from the same lot's root-cause result (test); templates tightened so every step names the
  tool it applies to.
- Dataset: 5,000 lots, generator seed 20260930, config hash `d0a22bd397163f56`.
