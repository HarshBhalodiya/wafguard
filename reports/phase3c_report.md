# PHASE 3C Report — Root-cause evidence graph

Date: 2026-10-14 · Brief: PHASE 3 (P2) §3C · Status: **COMPLETE** · Decision: **D-047**

PHASE 3 was 2/3 delivered (3A similarity, 3B multi-signal engine). 3C — the
evidence graph — did not exist in any form: `grep -rniE "evidence_graph|nodes.*edges"`
over `backend/app`, `backend/scripts` and `frontend/src` returned nothing, and no
route returned a graph. This phase adds it, additively: no existing endpoint,
service or test was modified except the root-cause router (which gained one route).

## What was built

| Piece | Where | What |
|---|---|---|
| Rules file | `backend/app/rootcause/graph_rules.json` | Engineer-readable rules: engine candidate → mechanism → defect pattern → yield-loss link, each with a written `justification`. **Not** Python constants. |
| Builder | `backend/app/rootcause/evidence_graph.py` | `build_evidence_graph(db, lot_id, top_n, root_cause=None)` → `{nodes, edges, gaps, …}`. Nodes are `equipment / chamber / sensor_issue / mechanism / defect_pattern / yield_loss`; every edge has `strength` ∈ [0,1], `kind` ∈ {`measured`, `rule`} and `evidence_ids`. |
| API | `GET /api/lots/{lot_id}/evidence-graph?top_n=1..10` | Same builder the script uses. Auth inherited from the root-cause router (401 anonymous, 404 unknown lot, 422 `top_n>10`). |
| Script | `backend/scripts/explain_evidence_graph.py` | Read-only CLI: prints the graph and writes `reports/evidence_graph_<lot>.json`. |
| UI | `frontend/src/components/EvidenceGraph.jsx`, mounted on `/root-cause` | Dependency-free layered SVG: solid = measured, dashed = rule, thickness = strength, per-edge justification on hover, gaps + disclaimer printed under the diagram. |
| Tests | `backend/tests/test_evidence_graph.py` (12), `frontend/src/components/EvidenceGraph.test.jsx` (4) | See verification below. |

## Verification (measured, this machine)

| Check | Result |
|---|---|
| `pytest -q` (full backend suite) | **181 passed**, 1 warning, 211 s — was 169 before this phase; **12 new**, 0 modified, 0 skipped. |
| `npx vitest run` (full frontend suite) | **40 passed** (12 files) — was 36; **4 new**. |
| Script against the live cluster (read-only) | exit 0. `LOT_11532` → **13 nodes, 15 edges, 1 gap**, artifact `reports/evidence_graph_LOT_11532.json`. |
| Invariant `every edge has evidence_ids` | True on the generated artifact (asserted in the test, re-checked on the JSON). |

### The measured example (`LOT_11532`, ETCH_007, yield 83.3%)

```
[measured] yield_loss    Yield outcome on LOT_11532 = 83.3
[measured] equipment     ETCH_007
[measured] chamber       ETCH_007_CH2
[measured] sensor_issue  Pressure = 0.5013  →  [rule] Chamber pressure instability driving particle generation
                                                →  [rule] Edge-Ring
[measured] sensor_issue  Temperature = 0.2193 → [rule] Thermal excursion with in-chamber particle condensation
                                                →  [rule] Donut, Edge-Loc
[measured] sensor_issue  Gas Flow = 0.1921    → [rule] Recipe-transition stress with a gas-flow offset
                                                →  [rule] Loc
measured edge: equipment:ETCH_007 → sensor_issue:pressure  strength=0.3756
rule edge:     sensor_issue:pressure → mechanism:chamber_pressure_instability  strength=0.4261
measured edge: pattern:Edge-Ring → yield_loss  strength=0.0826
```

Edge strengths are computed, never stored: `sensor→mechanism = candidate score × rule strength`,
`mechanism→pattern = rule strength` (× `0.4` when this lot's inspection does **not** corroborate it),
`pattern→yield_loss = max(defect_density, yield deficit)` — all measured on this lot.

## Honesty properties (each one test-enforced)

- **Rules live in a file.** `test_rules_file_is_valid_json_and_covers_the_engine_candidate_set`
  checks every rule's candidate exists in `CANDIDATE_DEFINITIONS`, names ≥1 defect pattern, and
  carries a readable justification.
- **Rule edges cite their rule.** `test_rule_edges_cite_a_rule_id_that_exists_in_the_file` —
  a `rule` edge must reference a `rule_id` that exists in the JSON.
- **No invented links.** `Voltage` and `Current` have no authored mechanism in the generator's
  physics table, so they get a node and a `gaps` entry, never a mechanism edge
  (`test_candidate_without_a_rule_gets_no_invented_mechanism_edge`).
- **No ground truth.** `test_no_ground_truth_is_read_by_the_graph_module` asserts the module
  never references `ground_truth`, `pattern_associations` or `data/synthetic`
  (`app/evaluation/` stays the only place allowed to read hidden labels).
- **Candidates, not causes.** Every payload carries a `disclaimer`; the UI repeats it.
- **A healthy lot is not called a loss.** The `yield_loss`-typed node's label states the actual
  outcome and its detail says explicitly whether a loss was observed.

## Defects found and fixed during the phase

1. **Rule edge could lose its own citation** (found only by the *full* suite — it passed in
   isolation, i.e. a test-order-dependent hole). When a lot's inspection did **not** corroborate a
   prediction, the `mechanism→pattern` edge was `kind: "rule"` but cited only measured
   `wafer_pattern` evidence, never the rule that produced it. Fixed at source: a `rule` edge now
   always carries its `rule_id` (plus any corroborating measured evidence). No assertion weakened.
2. **"Yield loss" node on a 97 %-yield lot.** The first live run (`LOT_10901`, yield 97.14) drew a
   node labelled "Yield loss". Relabelled to state the observed outcome and whether a loss
   actually occurred.
3. **Windows console crash.** Printing the graph's `→` on a cp1252 console raised
   `UnicodeEncodeError` and aborted the script. `stdout`/`stderr` are now reconfigured to UTF-8
   (with `errors="replace"`), so non-ASCII graph text can never abort a run again.

## Known limits (stated, not hidden)

- **2 of 10 candidates have no rule** (`Voltage`, `Current`) — deliberate; they appear as gaps.
- **Rules are authored physics, not learned links.** They restate
  `app/simulator/mechanisms.py`; they are not inferred from data. An engineer can edit them, which
  is the point — and also the risk: editing them changes the graph without retraining anything.
- **Corroboration needs persisted inspections.** In the live cluster the seeded lots carry no
  `wafer_map_pattern`, so `dominant_wafer_pattern` is `"None"` and every mechanism→pattern edge is
  rule-only (reported in `gaps`). The graph does not pretend the inspection corroborated it.
- **The `0.4` discount factor is a judgment call**, recorded in the module docstring, not derived.
- **Demo-narrative drift found (not fixed here):** the README and `submission.yaml` both cite
  `LOT_10901` as an "RF-power drift on ETCH_007" story. In the currently seeded dataset that lot
  is a 97 %-yield **LITHO_006** lot, so the demo line is stale. Flagged for the docs pass.
