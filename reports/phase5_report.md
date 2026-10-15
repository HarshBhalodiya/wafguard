# Phase 5 Report — Case-based knowledge base, control charts & similarity indexing

> **SYNTHETIC DATA demo.** Every count below is measured on the simulated dataset's own
> history. Case statistics are counts of what happened to similar lots — never a claim
> that an intervention caused a recovery (this dataset records no interventions).

## What changed

| Was | Now |
|---|---|
| Recommendation templates were an in-code dict with no measured case history — the audit's canonical fake ("9/12 similar cases recovered") had no real counterpart | Templates live in a documented data file (`app/knowledge/recommendation_templates.json`, provenance field included) and **every recommendation item carries measured `case_statistics`**: outcomes of the k most similar backward-looking same-step lots (P5-1) |
| No SPC anywhere (the problem statement's "manual single-parameter SPC" foil had no in-product counterpart) | Lot-level control charts per tool+parameter: running mean/σ from the tool's own prior history, R1/R2/R3 rules, `GET /api/equipment/{id}/control-chart`, and a Control Charts page in the UI (P5-2) |
| `similarity_service` docstring promised an "ANN upgrade in Phase 5" | Measured instead: brute-force over cached normalized matrices is 1.4 ms (temporal, 5,000×64), 0.12 ms (vision, 810×64), and the per-request parameter refit is 4.2 ms (5,000×7). No ANN dependency added; docstring now states the measurements (P5-3) |

## P5-1 — Case statistics (the real "similar cases" numbers)

- Neighbors: parameter-space kNN over the same process step, **restricted to lots
  processed strictly before this lot** (test-pinned: no forward peeking).
- Measured per neighbor: final yield, risk level; aggregated as `low_yield` /
  `healthy` / `healthy_despite_elevated_risk` (elevated = predicted risk ≥ 0.5),
  plus median yield and the raw neighbor list.
- Honest limits, enforced by tests: the word "recovered" appears nowhere (no
  intervention events exist to justify it); lots with < 3 comparable prior lots report
  `n_similar: 0` with an explanation instead of extrapolating.
- Wiring: `generate_recommendations` attaches the stats to the result and to every
  `recommendation_items` entry (alongside the Phase 3 `grounded_in` evidence ids), so a
  suggestion now answers "what happened to similar lots?" with counted outcomes.

## P5-2 — Control charts

- `app/services/control_chart_service.py`: per (equipment, parameter ∈ temperature,
  pressure, rf_power, gas_flow) over `{parameter}_mean` from prediction snapshots.
- Strictly backward-looking z-scores: each point is judged against the mean/σ of the
  points *before* it (min 8 prior points; below that, `sigma_reliable: false` and no
  rule flags — reported, not hidden).
- Rules: **R1** beyond 3σ · **R2** 2-of-3 beyond 2σ same side · **R3** 7-point trend.
  Zero-σ edge: a deviating point against a perfectly flat history is the sharpest
  possible breach (sentinel z), an identical point is at the centerline.
- API: `GET /api/equipment/{id}/control-chart?parameter=&limit=` (authed read; 404 for
  unknown equipment/parameter). Response carries centerline/UCL/LCL/σ, per-point z,
  breach list with rule + detail, and the granularity label
  ("lot-level telemetry means (not per-reading SPC)").
- UI: `/control-charts` page — equipment + parameter pickers, line chart with
  centerline/UCL/LCL reference lines, red breach markers, and the breach list spelled
  out; sidebar link under Vision & Equipment.

## P5-4 — Verification

- Backend (this phase's files): `test_phase5_knowledge.py` 6/6,
  `test_control_charts.py` 8/8 — including forward-peeking guard, "no recovery claim"
  guard, hand-built R1/R2/R3 series, zero-σ edge, API contract.
- Frontend: `ControlCharts.test.jsx` 2/2 (chart meta + breach list render from mocked
  API; prompt state before selection) — suite total **22/22**.
- Full suites: see `reports/phase5_testrun.log` (backend) and the vitest summary above.

## Known limitations / follow-ups

- Control charts are lot-granularity by design; per-reading SPC would need the raw
  48-sample series joined per tool+parameter — a data-access change, not a chart change.
- Case statistics use parameter-space neighbors only; folding in the temporal
  embeddings as a second neighbor space is a natural extension once Phase 3's
  multi-space search is needed for outcomes too.
- The z-score sentinel (±99) for zero-σ breaches is a documented convention, not a
  statistical quantity — it exists to keep the API JSON-serializable.
