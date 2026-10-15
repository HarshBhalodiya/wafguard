# WafGuard Upgrade — Decision Log

Running record of decisions made during the upgrade. One entry per judgment call that a future contributor would otherwise have to reverse-engineer. Newest at the bottom.

---

## D-001 (Phase 0) — Broken venv rebuilt on Python 3.14

**Found:** `backend/.venv` referenced a base interpreter (`C:\Users\harah\...\Python313`) that does not exist on this machine — the venv was created on another computer. Nothing Python-backed could run.

**Decision:** recreated the venv in place with the machine's Python 3.14.7 and installed `requirements.txt`. All 41 backend tests pass on it.

**Consequence:** project now runs on 3.14, not the README's "validated on 3.13". `requirements.txt` pins were loose (`>=`) so everything installed cleanly; if a dependency later needs ≤3.13, install 3.13 and recreate. Do not commit `.venv/`.

## D-002 (Phase 0) — README overclaims documented, not silently fixed

The README claims Slack alerting, watsonx.ai integration, and broad RBAC. None exist in code (alerts are DB rows; the LLM is Anthropic API with a deterministic fallback; only `/api/data/import` is role-gated). Per the upgrade rules ("be honest"), these claims get corrected in the README during Phase 1's doc update, and the AUDIT records them now. No code pretends to have these features.

## D-003 (Phase 0) — `.env` contains live Atlas credentials in plaintext

`.env` (tracked) contains a real `mongodb+srv://` URI with username/password. Flagged in the audit. **Not** auto-rotated or rewritten — that touches infrastructure the user owns. Recommendation stands: rotate the password, move to `.env.local` + gitignore, and point local dev at `mongodb://localhost:27017`.

## D-004 (Phase 0) — "partly fake" bar for the root-cause engine

The existing root-cause engine is a real weighted formula over six signals, but two signals (`WAFER_PATTERN_CANDIDATE_BOOST`, `_equipment_association` text-match) are authored heuristics rather than anything learned or measured. It is labeled **partly fake** in the audit because the upgrade brief's P2 requires every signal to carry *evidence with a source*, and a hardcoded lookup table cannot cite a source. Phase 3 replaces it; nothing breaks before then.

## D-005 (Phase 0) — Ground truth becomes a separate parquet, never a training column

The generator knows which fault it injects but today writes nothing down. Phase 1 writes `data/synthetic/ground_truth.parquet` with per-lot fault labels + onset times, and a test enforces that no module under `app/features/` or `app/ml/` imports it. Evaluation-only, per the brief's hard rule.

## D-006 (Phase 0) — Old synthetic dataset not preserved as a parallel copy

Phase 1's generator v2 writes into the same `data/synthetic/` paths with a `_schema_version: 2` marker. The v1 CSVs are reproducible from git history + the v1 script, which stays in `scripts/`. Keeping two live datasets would force every loader to branch on version for no benefit.

## D-007 (Phase 0) — Wafer die grids must get cheaper to store before 5k lots

At 950 lots × 25 wafers, storing 48×48 int grids as nested Mongo arrays already produces ~24k sizeable documents; scaling to 5,000 lots naively would bloat the DB. Phase 1 stores grids compressed (PNG bytes or RLE) and keeps full-resolution telemetry in parquet with summary/downsampled series in Mongo. Decision recorded now because it changes the `WaferMap` schema in Phase 1, not later.

## D-008 (Phase 0) — Tests that hardcode dataset scale get config-driven

`test_api.py` asserts `total_lots == 950` and `len(equipment) == 30`. Phase 1 changes both counts, so those assertions will be rewritten to derive from the simulator config (import the config, assert equality) rather than new magic numbers.

## D-009 (Phase 1) — Simulator v2 replaces the v1 generator in place

`app/simulator/` (config.json + typed loader, process_physics, mechanisms, generator, writer) replaces `scripts/generate_synthetic_data.py` as the dataset source. The v1 script is kept for provenance but is no longer the documented path. One command regenerates everything: `python scripts/build_dataset.py`. Seed and dataset layout are locked by `config.json` (`config_hash` in `_generation_metadata.json` is the dataset fingerprint).

## D-010 (Phase 1) — Sensor drift is deliberately out-of-spec by end-of-run

First tuning pass gave sensor drift a 0.35–0.60 half-width ramp — always inside the spec band. Such a run is invisible in telemetry yet the mechanism claims 7pp yield loss, so the classifier cannot honestly learn the link and the risk engine scores those lots LOW. Decision: drift bias magnitudes raised to 1.2–1.6 half-widths (linear ramp reaches out-of-band by the final samples). Physics rule going forward: **any fault that costs yield must leave a measurable signature**; test_simulator.py asserts drift lots exceed the band by run end.

## D-011 (Phase 1) — Risk-engine parameter deviation accepts v2 telemetry-aggregate keys

`compute_risk` was fed v1-style scalar keys (`rf_power`) by the API, but the v2 backfill snapshot carries telemetry aggregates (`rf_power_mean` / `rf_power_max`). The deviation signal was silently 0.0 in backfilled predictions. Fixed in `compute_parameter_deviation_score` to evaluate the worst of scalar/mean/max per channel. Lesson: one canonical feature-snapshot key style is on the Phase 2 list.

## D-012 (Phase 1) — Investigation tests anchor on a test-split faulty lot

The original `incident_lot_id` fixture took the first rf-power-drift lot chronologically — a *training* lot the model memorizes, scoring it LOW risk. Fixture now picks the first faulty lot inside the chronological **test** split, which matches reality (investigations target recent, unseen lots) and keeps tests honest about model behavior.

## D-013 (Phase 1) — Wafer die grids are derived, not stored (schema v2)

Instead of shipping 125k nested 48×48 arrays in Mongo, `app/services/wafer_map_store.py` materializes a wafer's grid deterministically from (wafer_id, spatial_signature, defect_density) with a fixed hash seed. Same wafer → same map on every load; faulty lots visibly show their mechanism's signature pattern. Storage drops by ~100×.

## D-014 (Phase 1) — Remote Atlas seeding made resumable, credentials redacted in logs

User approved reseeding the shared Atlas cluster. The first full attempt died twice: a 1.5s server-selection timeout (too tight for Atlas handshakes — now 10s for scripts) and a connection drop mid-2.2M-doc bulk insert. `seed_database.py` is now fingerprinted (`seed_state` doc per dataset config_hash), inserts with deterministic `_id`s in 10k batches with transient-error retry, and skips already-complete collections on re-run. The connection logger now redacts credentials (`***@host`) after the plain-text URI surfaced in a log line.

## D-015 (Phase 1) — Mongo stores a bounded per-lot defect sample; full table stays on disk

The shared Atlas cluster is M0 free tier (512 MB). The full defect table (2.2M rows) alone needs ~550 MB, and a mid-upload run tripped the quota ("writes are blocked"). Decision: `seed_database.py` inserts the first 50 defects per lot (203,751 docs, ~55 MB) into Mongo for the UI/API, controlled by `SEED_MAX_DEFECTS_PER_LOT` (0 = full load), and records the cap in the `seed_state` note. The complete table remains in `data/synthetic/defects.csv` and is the source for all ML/evaluation work from Phase 2 on. If the cluster is ever upgraded, set the env var to 0 and re-run the seed (fingerprint stays, collections reload).

Also in this pass: the seeder's resume became **monotonic** (partially-inserted collections keep their docs and only the remainder is inserted — deterministic `_id`s dedupe), and `_records()` sanitizes numpy arrays/scalars (the events parquet carried a raw `np.ndarray` in `payload`, which BSON rejects).

## D-016 (Phase 2A) — Extended tabular feature set (38 features) built behind the same temporal rules

`app/features/tabular.py` adds 15 features on top of the Phase 1 set: per-channel OLS slope, excursion count and time-outside-range (vs `PROCESS_PARAMETER_RANGES`, the single source of truth), plus equipment age, chamber-previous-yield and prev-10-low-yield-rate — all backward-looking (shifted/rolling series only). `NUMERIC_FEATURE_COLUMNS` grows to 38; training and runtime inference share one code path (`build_lot_features_bulk` / `single_row_to_model_matrix`), so the API feature snapshot and the models can never drift apart. Backfill snapshots now carry the extended keys.

## D-017 (Phase 2A) — HPO is a fixed-budget, seeded, validation-only search

Optuna (TPE, seed 42, 40 trials per model by default, 5 in tests) searches a small physically-bounded XGBoost space and scores ONLY on the chronological validation split; the test split is first touched exactly once per model, after training. Studies are in-memory and reproducible. Chosen params are recorded in the model metadata (`metrics.hpo`), so "how was this tuned" is always answerable from the artifact.

## D-018 (Phase 2A) — Production classifier ships calibrated, and the decision threshold is re-picked after calibration

XGBoost probabilities were under-confident (val ECE 0.156 on the 5k dataset). The shipped artifact is now `CalibratedClassifierCV(FrozenEstimator(XGB), isotonic)` fit on validation (sklearn ≥1.6 prefit pattern). Because calibration rescales scores, 0.5 is no longer the F1-optimal operating point — `find_best_threshold` re-picks it on validation only (0.475 on the 5k run) and it is stored in metadata and used consistently at inference. Two consequences handled in code: (1) SHAP attribution unwraps the calibrator to the inner XGBoost (monotone remap ⇒ identical attributions); (2) evaluate_classifier records the threshold used alongside every metric.

## D-019 (Phase 2B) — Temporal models: mask-aware, train-only scaling, standardized targets; Transformer ships

`app/features/temporal_dataset.py` builds (N, 48, 4) sequences (T is data-driven: 48 telemetry steps/channel) with explicit missing-value masks; simulator NaN dropouts are excluded as *absence*, not zeros. Per-channel scaling and yield-target standardization are fit on the train split only. Both TemporalGRU and TemporalTransformer (comparison) train with early stopping on the same chronological split. Result on the 5k test split: GRU risk F1 0.639 / Transformer 0.622; yield R² GRU 0.418 / Transformer 0.423 — the combined two-head criterion ships the **Transformer** encoder; weights in `temporal_gru_state.pt`, embeddings per lot in `temporal_embeddings.parquet`. Honest anchor: the temporal models beat the tabular XGBoost on risk (F1 0.43 at its stored threshold on this run) but yield R² is a tie — sequence models earn their keep on within-lot shape, not aggregate level.

## D-020 (Phase 2C) — Vision architecture comparison done; production artifact stays WaferCNN

SmallResNet and SmallViT trained identically to WaferCNN (same procedural dataset, same 60/20/20 stratified split, seed 99). Test macro-F1: WaferCNN 0.896, SmallResNet 0.669, SmallViT 0.908. SmallViT nominally wins (+0.012 macro-F1) but the shipped `wafer_pattern_cnn` artifact remains a WaferCNN: the runtime loader (defect_service) constructs WaferCNN, the margin is small, and swapping backbones is a deliberate loader change — recorded here instead of done silently. Per-class recall/F1 and confusion matrices are in `reports/vision_comparison.md`; ALL maps are synthetic (WM-811K-taxonomy generator), so these numbers cannot be read as real-fab performance. Embeddings for similarity search exported from the best encoder (`wafer_embeddings.npz`).

## D-021 (Phase 3) — Wafer-pattern evidence is a measured, versioned table (audit D-004 cashed in)

The authored `WAFER_PATTERN_CANDIDATE_BOOST` dict (Edge-Ring → RF 0.70 etc.) is deleted. In its place `scripts/build_evidence_tables.py` measures P(candidate|pattern) from the generator's hidden labels **on the chronological train split only**, with fractional credit for multi-mapped faults, Laplace-style smoothing, raw counts, `n_lots`, a config hash and a source citation, written to `models/artifacts/evidence/pattern_cause_associations.v1.json`. The service loads the artifact and returns signal 0 with an honest note when it's absent. The leakage guard (`tests/test_ground_truth_leakage.py`) forbids `ground_truth` under `app/services`, so the table is built evaluation-side (`app/evaluation/`) and consumed only as a versioned artifact. Consequence: the measured Edge-Ring association is genuinely ambiguous (Pressure 0.52 vs RF Power 0.48 on the 5k train split) — the table shows the ambiguity instead of the old dict pretending to disambiguate.

## D-022 (Phase 3) — Equipment association = measured relative rates with shrinkage, not text-match

The old `anomaly_status` free-text match (0.9/0.4/0.1 authored constants) is replaced by per-candidate rates measured from `timeline_events` + `maintenance` + `lots`, strictly before the lot's own `process_start`: per-parameter excursion rate, unscheduled-repair+alarm rate, days-since-PM vs the fab's measured median PM cadence, and prev-10 low-yield rate vs the fab baseline. Each is scored relative to the fab baseline with empirical-Bayes-style shrinkage (10 pseudo-lots), so low-N tools are pulled toward "no signal" instead of being over-trusted; sample sizes and the `low_sample` flag travel in the evidence label.

## D-023 (Phase 3) — Extended features join SHAP groups; temporal evidence becomes per-channel shape

The Phase 2A columns (`{p}_slope`, `{p}_excursions`, `{p}_time_outside_pct`) now join each candidate's SHAP group (previously unattributed) and excursion counts appear as their own FACT evidence items. The temporal signal no longer reduces the run to one last-minus-first delta: it combines OLS ramp, end-drift and time-outside-range, and the evidence label names the measured shape (steady ramp / step change / persistent out-of-band), so a step and a ramp are no longer indistinguishable to the reader.

## D-024 (Phase 3) — Recommendations must ground in evidence ids; unsupported candidates are dropped; multi-space similarity added

Every evidence item carries a stable `evidence_id` (`rc:{lot}:{candidate-slug}:{idx}`) and every recommendation item carries `grounded_in` referencing them — a suggestion with zero supporting evidence cannot be produced (pre-Phase-3 cached results without ids trigger recompute). A root-cause candidate with **no** evidence items is dropped from the ranked output rather than shown as an unhedged cause (signals and evidence cut-offs match 1:1, so this drops exactly the all-signals-near-zero cases). Recommendation templates were tightened so every rendered step names the tool it applies to. Similarity: `find_similar_multi()` searches the parameter kNN **plus** cosine space over the Phase 2 temporal + vision embeddings, each match tagged with its space, exposed at `GET /api/similarity/{lot_id}`; the old `/api/similar/{lot_id}` is untouched. Vision-space coverage is reported honestly (810 of 5,000 lots have wafer embeddings).

## D-025 (Phase 3) — equipment_degradation Top-5 regression documented, weights NOT test-fitted

The validation harness (P3-7, `reports/phase3_report.md`) shows the multi-signal engine improves Top-3 over the SHAP+deviation baseline (0.742 → 0.802) but regresses Top-5 (1.0 → 0.841) and MRR is a tie (0.772 vs 0.780). The regression is entirely `equipment_degradation` (probe: tool-condition candidates are never dropped by the evidence filter; their single strong measured equipment signal, weight 0.15, loses to parameter candidates stacking 3–4 weak signals ≈0.25–0.30). The fix would be a max-signal term or a dedicated tool-condition candidate — a weights/candidate-set change. **Not done in Phase 3**: tuning weights against this test split would be fitting the benchmark. Carried as the first item of the next engine pass, with this same harness as the acceptance check.

## D-026 (Phase 4) — Phase 4 tracker list was lost and reconstructed from cross-references

A session restart destroyed the original Phase 4 to-do list (it lived only in chat; every later phase doc that cited it survived). `docs/phase4_plan.md` reconstructs the scope from its surviving cross-references — "RBAC roles added in P4" and "the P4 dispatcher" (Phase 8), "the P4-5 fix" (Phase 9), "LLM-provider honesty is Phase 4 (P4-7)" (phase3_plan.md) — plus the audit's standing gaps. Recorded loudly because a *different* master plan would re-cut this phase; nothing in later phases depends on Phase 4 internals beyond the dispatcher and the role names.

## D-027 (Phase 4) — RBAC: auth on every route, explicit per-action role matrix

All routers except `/api/health` + `POST /api/auth/login` now require a valid JWT (router-level dependency in `main.py`); the `viewer` role is finally a real read-only role. Mutations carry explicit gates: what-if → process/yield/admin; copilot → all engineer roles + admin; defect analyze → process/equipment/admin; data import → admin+process_engineer (unchanged); alert-channel admin + scan + redeliver → admin. Test-fixture clients log in as admin; the anonymous matrix lives in `tests/test_rbac.py`. Consequence for humans: Swagger needs a login token first (README documents it). The gate is demo-grade by design — no refresh/revocation, one shared secret — and `utils/security.py` says so.

## D-028 (Phase 4) — Alerts dispatch through pluggable channels; generic webhook, not "Slack"

`alert_dispatch_service.raise_alert()` is now the single path for creating an alert (train backfill + admin scan route through it). Channels: `log` (structured record + delivery row, zero-credential demo default), `webhook` (generic JSON POST — deliberately vendor-neutral: a Slack incoming webhook works, and so does anything else that accepts JSON), `smtp` (optional). Every attempt lands in `alert_deliveries`, so "did anyone receive this?" is answerable; webhook failures retry 3× then record `failed`; dispatch NEVER raises (a dead channel must not break training or the API — same degrade-rule as the copilot). Channel listings mask webhook targets.

## D-029 (Phase 4) — Copilot names its phrasing provider; secret-scan guards the repo

`answer_question` returns `llm_provider` (`null` / `anthropic` / `openai_compatible`) and `source: llm:<provider>`. The openai_compatible adapter takes `LLM_BASE_URL`, so any OpenAI-style endpoint (self-hosted, or a watsonx-compatible one) works and is reported as what it is — no vendor is named anywhere that isn't configured. The deterministic template remains the default and the no-credentials path. Separately, `tests/test_secret_scan.py` scans all git-tracked files for credential-shaped strings (Atlas URIs, API-key shapes, private-key blocks) and asserts `.env` stays untracked — D-003's hygiene is now enforced by the suite, not by hope.

## D-030 (Phase 5) — Case statistics are measured outcomes, never a recovery claim

`app/services/case_statistics.py` measures what actually happened to a lot's k most similar backward-looking same-step neighbors: low-yield count, healthy count, and "healthy despite elevated predicted risk" — attached to every recommendation item. There is deliberately **no "recovered after fix" statistic**: the dataset records no interventions, so a recovery rate could only be invented (the exact fake the Phase-0 audit flagged). A test pins both properties (no forward peeking; the word "recovered" appears nowhere). Templates moved to `app/knowledge/recommendation_templates.json` with a provenance field; counts are measured at render time because counts stored in a data file would rot.

## D-031 (Phase 5) — Control charts are lot-level, honestly labeled; SPC rules are documented constants

`control_chart_service` charts `{parameter}_mean` per lot against the tool's own prior history (each point judged only against earlier points, min 8 for σ). This is **not** per-reading SPC and the API/UI say so — claiming per-reading SPC from lot means would be the overclaim this project refuses. Rules R1/R2/R3 (3σ / 2-of-3 at 2σ / 7-point trend) are classic Western-Electric-style constants defined in code; the zero-σ edge (deviation against perfectly flat history) uses a documented ±99 sentinel z to stay JSON-serializable.

## D-032 (Phase 5) — ANN index for similarity deliberately NOT added; the docstring promise was retired by measurement

The Phase 3 docstring promised "the ANN upgrade in Phase 5 targets the embedding spaces first". Measured on the real 5k artifacts: cached normalized cosine is 1.4 ms (temporal) / 0.12 ms (vision), and the per-request parameter scaler+kNN refit is 4.2 ms. An ANN index (FAISS etc.) would add a native dependency and indexing latency to save single-digit milliseconds. Decision: keep brute force, document the measurements in the docstring and report; revisit only if the dataset grows past ~10⁶ lots.

## D-033 (Phase 6) — Phase 6 tracker list was lost and reconstructed from cross-references

A session restart destroyed the original Phase 6 to-do list (like Phase 4's, D-026, and Phase 5's). `docs/phase6_plan.md` reconstructs the scope from its surviving cross-references: the audit's undelivered Phase-1 promise (*"one-command `python scripts/build_dataset.py` runs generate→seed→train→evaluate"*, AUDIT §8.5), the Phase-2 report's undelivered follow-up (*"retrain→backfill as one documented command chain; add a smoke test that the API serves the fresh metadata version"*), its known problem 2 (*pytest overwrites `models/artifacts/`*), Phase 4's note that the one-command pipeline talks to Mongo directly, and the stale `docs/setup-guide.md` (read by the evaluation pipeline). Recorded loudly because a *different* master plan would re-cut this phase.

## D-034 (Phase 6) — The one-command chain is a flag on build_dataset.py, not a new script and not a new default

`build_dataset.py --all` runs generate→seed→train→evaluate; `--stages`, `--from` and `--plan` compose. Two constraints shaped this: (1) the audit names `build_dataset.py` as the one command, so a separate orchestrator would have left the promise half-true; (2) the bare command must keep generating only — seeding touches a shared database and training takes minutes, so making them implicit would break every existing doc, test and the README's step-by-step flow. `resolve_stages()` is a pure function and `run_pipeline()` takes injectable runners, so the resolution and fail-stop rules are tested without training anything (a test caught and fixed the bare-`--from`-implies-empty-chain bug).

## D-035 (Phase 6) — /api/health now means "serviceable", and its readiness view is deliberately shallow

`status: healthy` requires DB **and** the trained tabular bundles; a DB-up-but-untrained deployment reports `degraded` with the fix command — the previous DB-only green light was a lie of omission (every prediction endpoint 503s without bundles). Because the route is unauthenticated (D-027 exemption), the readiness block carries only booleans, served version strings and the dataset `config_hash` — no filesystem paths, no collection counts; a test pins that boundary. `scripts/doctor.py` renders the full detail (fix commands, per-bundle state) from the same `app.ops.readiness` functions, so the doctor, the health endpoint and the docs cannot drift apart.

## D-037 (Phase 7) — Phase 7 tracker list was lost and reconstructed from cross-references (same pattern as D-026/D-033)

A session restart destroyed the original Phase 7 to-do list. `docs/phase7_plan.md` reconstructs the scope from surviving cross-references — the Phase 3 report's explicit anchor (*"a latency budget + caching belongs to Phase 7 (P7-6)"*), Phase 6's readiness/doctor handoff (D-033…D-036: the bootstrap-and-state picture; the surviving gap was live runtime metrics), and the D-032 measurement-first precedent. Recorded loudly; the user re-affirmed the reconstruction before build.

## D-038 (Phase 7) — One process-owned MetricStore behind a single writable seam; tests never farm global state

All hierarchical timing state lives on `MetricStore` instances. Exactly one instance exists per process (request spans, counters), in `app/ops/registry.py` — placed there rather than in `app.main` because the ops routes must import it and `main` imports the routes (a circular import otherwise). The registry exposes `swap_store()` as the ONLY writable seam, used by tests to isolate and restored at teardown; production code only reads the registry reference per request. The legacy `app.utils.metrics` p50/p95 deque is untouched — the Phase 3 `/api/health` contract is asserted still-true by test rather than replaced.

## D-039 (Phase 7) — The latency budget was fixed by killing round-trips, not by caching SHAP results; numbers changed nothing else

Profiling against the live Atlas cluster showed the P3 baseline (2.8 s p50 / 3.5 s p95) was NOT dominated by the SHAP pass after first call (~16 ms warm) but by ~31 network round-trips from per-candidate `count_documents` fan-out. Two changes: (1) `shap_service` caches one `TreeExplainer` per model object (attribute-keyed, GC'd with the model; cold-start cost is amortized, per-call reuse is the win); (2) `_prefetch_equipment_stats` replaces the 23-round-trip per-candidate fan-out with 4 round-trips in one bundle. Measured: 2.9–3.98 s → ~0.4–1.5 s warm on `LOT_10000`. Correctness gate: `_equipment_association`'s legacy and prefetch paths are asserted to produce identical scores, and live content is identical modulo per-call timestamps — a pure timing refactor, not a scoring change. The remaining latency is network-bound (remote Atlas); a same-DC number is NOT claimed.

## D-040 (Phase 7) — Ops numbers are deliberately in-process-only and the UI says so

`/api/ops/metrics` and `/api/ops/health-probe` are admin-only (RBAC tier above viewer, tested 401/403/200) and expose only API-process telemetry: per-endpoint latency aggregates and counters from the ring buffer, no DB contents, no filesystem paths (asserted by a leak-guard test). The numbers reset when the API process restarts; the panel, the `health-probe` note, and this decision all state that honestly rather than implying a durable monitoring system. No external APM was added (D-027 lineage: demo-grade by design).

## D-036 (Phase 6) — The test suite no longer writes to models/artifacts/; the full --all chain was NOT re-run against the shared Atlas

conftest now copies the real artifacts into a session tmp dir and points `settings.model_dir` there — phase2's known problem 2 (a pytest run silently replacing production artifacts) is fixed, and proven by an artifact-mtime diff across the full 140-test run (identical). Separately, the `--all` chain was deliberately **not** executed end-to-end against the live Atlas cluster: generate would overwrite the seeded 5,000-lot demo dataset, and reseeding a shared cluster was a Phase-1 user-approved one-off (D-014), not a standing authorization. What is verified instead: stage machinery via injected runners, `--plan` live, the metadata smoke test (`GET /api/models` == `MODEL_VERSION`), and `doctor.py` against the real environment (13 OK, exit 0).

## D-041 (Phase 8) — Triage status is derived for legacy rows; resolved is terminal and requires acknowledge first

The alert lifecycle is `open → acknowledged → resolved`, enforced by `alert_triage_service` (illegal and duplicate transitions are typed 422s, not silently swallowed). Rows raised before Phase 8 — including the entire live backfill on the shared Atlas — carry no `status` field and are **derived** (`acknowledged_by`/`resolution` → open/acknowledged/resolved) both in Python and as a Mongo query for the status filter, so no destructive backfill was run. `resolved` is deliberately terminal and acknowledge-before-resolve is mandatory: the trail must answer "who picked this up" as well as "who closed it". A `reopen` transition was considered and left out — adding it later is a clean cut, not a half-built branch.

## D-042 (Phase 8) — Triage notes may not claim lot outcomes; the audit trail is append-only Mongo rows

Every transition appends one `{from, to, actor, at, note}` event to `alert_events`; events are never updated or deleted, and rejected transitions append nothing. `resolve()` (and `acknowledge()`) reject notes containing recovery-claim wording ("recovered…", "cured…") because the generator records no interventions — a recovery rate is unobservable in this dataset, exactly the invented statistic Phase 0 flagged (D-030). The guard is a wording boundary, not a semantic proofreader, and the trail is honest Mongo rows, **not** a tamper-proof ledger — both stated in the report and the UI copy. Dispatcher untouched: triage writes no delivery records (test-pinned against the P4-2 contract).

## D-043 (Phase 8) — Equipment SPC context is one small request per tool, fetched client-side; no batching endpoint was added

The equipment-health cards show "SPC (lot-level): N breaches" from the existing P5-2 control-chart API (rf_power, limit 40), fetched after the list renders; the detail panel fetches all four parameters on selection. A server-side batching endpoint would have been new surface beyond the to-do, and computing chart math client-side was not an option, so the N-small-requests trade was taken deliberately (~30 requests on the seeded fab, failures omit the chip rather than break the page). Labels stay honest per D-031: lot-level means, never per-reading SPC, and the generator's static `health_score` is presented unchanged beside it, not reinterpreted by it.

## D-044 (Phase 9) — CI validates the repo side of CI and gates hermetic-only suites + a fast-fail secret scan; the first proof is the first push

Phase 9's audit-first order: a bare checkout with no pre-seeded data/artifacts → `pip install -r requirements.txt` (24m13s, torch dominates on a slow local pipe) → two doc-to-reality fixes before the workflow was written, so nothing downstream depended on a fix that had not actually landed in the repo: (1) `frontend/vite.config.js.timestamp-*.mjs` was a Vite temp artifact that once slipped into the working tree untracked-and-unignored — got a `.gitignore` rule and was deleted; (2) `data/secom/*` and `backend/models/artifacts/*` are both gitignored, yet the suite loads SECOM from disk and the wafer-vision tests load the CNN artifact — the honest fix is a documented CI prep step (`scripts/download_secom.py` verified at 13s exit 0; `scripts/train_wafer_model.py` verified at 50s exit 0, procedural synthetic maps seed 99, no download), not committing large data/artifacts or weakening tests. Full backend suite from the clean tree: 169 passed, 1 warning, 5m07s, exit 0. Workflow: `.github/workflows/ci.yml` with a `secret-scan` job (named gate, pytest with `--noconftest`, verified 2 passed in 0.18s — so no heavy imports needed, credential-shaped commits fail loudly) plus `backend` and `frontend` jobs, none requiring secrets or services (every config default is safe, copilot has a deterministic fallback, dispatch stays on the no-network `log` channel). Mongo-touching pipeline stages are explicitly not CI (D-014/D-036 lineage). Two honest limits are cut hard: (a) CI here verifies the *repo side* of CI — what a clean checkout sees and whether the suites/build can run without secrets — *not* the first outcome of a workflow run, so the first real proof is the first push; and (b) the README badge is a placeholder + replacement rule until that first green run exists, because inventing a green badge before it would be exactly the "claim but don't verify" the audit flagged.

## D-045 (Phase 10+11) — The combined plan's cross-references were not on disk; the phase stops at a user gate instead of inventing a spec

`docs/phase10_phase11_plan.md` was written from a session that had the upgrade brief in chat but no brief file on disk. On 2026-10-14 a direct-read audit of that plan's own citations found that **two of its three anchors do not exist**: there is no `docs/wipbench.md` (`rg -i wipbench` matched only the plan at audit time), `reports/phase1_report.md` has no numbered next-phase list and never mentions WIPBench or a SECOM "dial-a-fault" (`rg -i dial` matched only the plan at audit time), and the "Historical Lot Similarity Search / Cosine" phrase the plan attributes to `docs/problem-statement.md` is actually a `README.md` feature row. The brief itself is real and repeatedly cited (`docs/AUDIT.md` line 15 names the "P0–P10 list"; D-004 quotes its P2), but no copy exists on disk — it is lost to session mortality like the Phase 4/5/6/7/8 trackers (D-026/D-033). The plan's recorded Phase 10 spec is also internally inconsistent (4 machine types in one sentence, a 5-machine run and a 3-machine baseline in others; "≤7 numeric feature columns" against "5 parameters × 1 mean per machine"). **Decision:** do not build Phase 10 or 11 to a reconstructed spec, and do not write a Phase 10/11 report claiming delivery. The honest completion of this turn is (a) the corrected tracker with the audit and an explicit decision gate (Gate A = paste the brief's Phase 10 text, or authorize a spec-configurable build labeled provenance-unverified; Gate B = pick the Phase 11 destination among SECOM dial-a-fault / condition-based monitoring / cognitive-control loop, or paste the brief's Phase 11 text), and (b) the shared dashboard-token prep documented against the existing `POST /api/auth/login` + `create_access_token` path — no new, weaker token surface. This keeps the project's own rule ("if the master plan differs, re-cut now; nothing is implemented yet") instead of producing a fabricated benchmark and calling it Phase 10.

## D-046 (Phase 11) — Destination chosen: SECOM dial-a-fault; the scope still waits on the brief, so no code is written yet

The user resolved Gate B on 2026-10-14: Combined Phase 10+11 builds **Phase 11 = SECOM dial-a-fault** (option 1 of the three recorded candidates), over condition-based monitoring and the cognitive-control loop. Gate A (the Phase 10 WIPBench spec text) was answered with "paste the brief's Phase 10 text", but no text has been supplied yet, so Phase 10 stays blocked. Because Phase 11's *scope* (its end state) is also defined only in the brief that is not on disk, **no Phase 11 code is written this turn either** — a wrong guess at "dial-a-fault" is the same fabrication risk as the Phase 10 benchmark. What is recorded instead is the verified starting position so the build begins from measured facts: real SECOM data **is** present (`data/secom/secom.data` + `secom_labels.data`, 1,567 rows each), the loader and preprocessor exist (`app/data/loaders/secom_loader.py`, `app/data/preprocessing/secom_preprocessor.py`), the classifier artifact is live (`secom_failure_classifier_v2.0.0.joblib` alongside v1.0.0), and the honest "before" metric is already measured (P/R 0.0, ROC-AUC 0.56 on real labels — `reports/phase2_report.md` known problem 5). Next action is the brief paste for BOTH phases' acceptance criteria; only then do steps 1 and 4 start.

## D-047 (PHASE 3C) — The evidence graph's causal links live in a JSON file an engineer can edit, not in Python; rule edges are visually and structurally distinct from measured ones

PHASE 3 was 2/3 delivered (3A similarity, 3B engine); 3C existed in no form. The graph is built from **two different kinds of link, kept visibly apart**: `measured` edges (this lot's feature snapshot, inspection pattern, defect density and yield) and `rule` edges (the candidate → mechanism → defect-pattern physics restated from `app/simulator/mechanisms.py`). The rules live in `backend/app/rootcause/graph_rules.json` with a written justification per rule because the brief requires "store rules in a file so an engineer can read them" — the cost, recorded here, is that editing that file changes the graph without retraining anything, and the links are authored physics rather than anything learned. Edge strengths are always computed, never stored: `sensor→mechanism = candidate score × rule strength`, `mechanism→pattern = rule strength × 0.4` when this lot's own inspection does **not** corroborate the prediction (0.4 is a judgment call, documented in the module docstring, not derived), and `pattern→yield_loss = max(defect_density, yield deficit)`. Two behaviours are deliberate refusals rather than gaps: `Voltage` and `Current` have no authored mechanism in the generator's physics table, so they get a node and a `gaps` entry rather than an invented mechanism edge; and a lot whose yield is above the threshold is labelled with its actual outcome, never "yield loss". Three defects were found and fixed at source during the phase: (1) a `rule`-kind `mechanism→pattern` edge could cite only measured pattern evidence and never the rule that produced it — caught only by the **full** suite because it passed in isolation, so a `rule` edge now always carries its `rule_id`; (2) the yield node mislabelled a healthy lot; (3) the CLI script aborted with `UnicodeEncodeError` printing `→` to a cp1252 Windows console, fixed by reconfiguring the streams. The module is test-pinned never to read `ground_truth`/`pattern_associations` — `app/evaluation/` remains the only place allowed to touch hidden labels. Verified: backend **181 passed** (12 new), frontend **40 passed** (4 new), script run read-only against the live cluster (`LOT_11532` → 13 nodes / 15 edges / 1 gap, `reports/evidence_graph_LOT_11532.json`).

## D-048 (brief PHASES 4–11) — The brief's phase numbers now govern; the repo's earlier "Phase 4–9" reports describe different work

The repo's `reports/phase4_report.md` … `phase9_report.md` (access control, case statistics, pipeline tooling, ops metrics, alert triage, CI) were built from reconstructed trackers (D-026/D-033/D-037), not from the brief's Phases 4–9. When the user pasted the brief and asked for every pending phase to be completed, a gap analysis against the code (not the report names) found: Phase 4 (lot risk) mostly done, Phases 5/8/9 partial, Phases 6/7/10/11 absent. This also resolves D-045/D-046: the pasted brief defines Phase 10 as the decision-centred UI and Phase 11 as the evaluation benchmark, so the WIPBench / SECOM dial-a-fault plan in `docs/phase10_phase11_plan.md` is superseded (kept for history). New work is reported under the brief's numbering in `reports/brief_phases_4_11_report.md`.

## D-049 (brief Phase 4) — Pre-run history is rebuilt as of the planned start; the recipe drives the step; similarity is fit on priors only

Three gaps in the existing engine were closed. (1) The proxy lot's stored history features describe the state one run *before* the proxy, so the planned run's `previous_lot_yield` was the wrong lot; `state_as_of()` now recomputes every history feature (previous yields, prior low-yield rate, days since maintenance, failure count, tool age) from rows strictly before `planned_start`. (2) `recipe_id` was echoed but never used: it now resolves the process step (the model's categorical input), and its prior outcomes are reported as a factor; a recipe/step contradiction is refused. (3) The similar-lot search used a scaler fit over *all* lots, so future lots shifted distances; the pool and scaler are now prior-only. The response also exposes the calibrated `probability_low_yield` separately from the blended risk score, and the upcoming queue reads the prediction table once instead of once per tool. The future-mutation test now covers predictions and maintenance as well as lots.

## D-050 (brief Phase 5) — Intervention history is a simulated overlay; recovery statistics are allowed only there, and labelled

D-030 refused a "recovered" statistic because no interventions existed. The brief asks for a table of historical interventions in the synthetic data, so `app/simulator/interventions.py` simulates one engineer response per low-yield lot (12 h tool cooldown): a diagnosis (fits the hidden fault with p = 0.70), a KB action, and a verification-lot yield drawn from a documented efficacy table. It is a separate RNG stream (the lot data is byte-identical), never alters lots, and stores the engineer's *diagnosis*, never the true fault. Recommendation = KB action (`app/knowledge/engineering_kb.yaml`, text only) + counts over interventions strictly before the lot with the same cause and action; ranking = candidate score × Laplace-smoothed recovery so 1/1 cannot beat 30/40; n < 10 carries a warning. `case_statistics` (D-030) is unchanged and still makes no recovery claim — the claim lives only in the new engine, next to a SYNTHETIC label.

## D-051 (brief Phase 6) — Uncertainty = bootstrap ensemble spread + split-conformal interval with measured coverage

20 bootstrap XGBoost regressors (production hyper-parameters, train split) give P10/P50/P90 for yield before, after and for the per-member difference; a split-conformal 80% band (validation residuals) gives the outcome interval, and its coverage on the test split is measured at training time and returned with every answer. A sensor change shifts `{p}_mean` and `{p}_max` by the same delta and holds the run's shape — a stated modelling choice. Limits live in `app/whatif/limits.json`: recipe window = the in-spec band, physical limit = the generator's clip range; outside either → 422. `etch_time/voltage/current` are not overridable (constant in v2 data). The legacy `/api/what-if` keeps its behaviour except that physically impossible values are now refused too.

## D-052 (brief Phase 9) — Jobs run on an in-process thread pool with status in Mongo; /api/v1 is the versioned contract and /api its alias

The brief allows "FastAPI background + Mongo job status". A two-worker `ThreadPoolExecutor` with `jobs` documents (queued → running → succeeded | failed) needs no extra service; the cost — jobs die with the API process — is handled by `recover_orphans()` at startup (marked failed, never left hanging). Swap in RQ/Celery behind the same store when jobs must outlive the process. Every router is mounted under `/api/v1` (in OpenAPI, with the error envelope documented on every route) and under `/api` (hidden alias, so the UI and old clients keep working). Envelopes carry `api_version`; errors carry `request_id` (also `X-Request-ID` and every log line). Models load once at startup (`app/ops/warmup.py`) and `/api/health` reports which. Docker files were written but **not built or run here** (Docker is not installed on this machine) — the first `docker compose up --build` is their first real test. CI: the invalid `fail-on-error` step key was removed.

## D-053 (brief Phase 7) — Validation on ingest, PSI+KS drift against the training split, back-filled daily monitoring, optional MLflow

Validation (`app/quality/validation.py`) runs in the seeder, the data-import route and a background job, storing one report per lot. Drift compares the training split (first 70% by time) with the last N days: PSI over 10 reference-quantile bins plus a KS test; a feature is flagged when PSI ≥ 0.25, or KS p < 0.01 with PSI ≥ 0.10 (KS alone flags trivia at n in the thousands). Monitoring snapshots are back-filled one per day because the dataset is historical; ECE uses a 14-day trailing window because one day holds too few lots. MLflow uses a local file store and is a logged no-op when unavailable — tracking must never fail training. Cumulative features (failure count, days since maintenance) are expected to drift by construction; the report shows that rather than hiding those features.

## D-054 (brief Phase 8) — The copilot composes cited sentences itself; an LLM may only rephrase, and its output is validated

Retrieval first: each evidence item has a stable id (`pred:`, `shap:`, `rc:`, `evt:`, `eq:`, `maint:`, `defect:`, `sim:`, `rec:`, `doc:`). A deterministic composer writes every sentence with its citations; this is the no-LLM answer. A configured provider (one `LLMProvider` interface; Anthropic, any OpenAI-compatible endpoint incl. watsonx-compatible gateways, or a registered custom one) receives the draft and the evidence and must return JSON sentences; the output is rejected — and the draft used — if it cites an unknown id, states a number without a citation, introduces a number not in the draft, or drops citations. Required evidence per intent is declared; when it is missing the answer starts "Not enough evidence". Doc retrieval is IDF-weighted token overlap over `docs/*.md` sections — no vector store. The old `/api/lots/{id}/copilot` endpoint is unchanged.

## D-055 (brief Phase 10) — Four decision screens added; every older route stays

`/investigate`, `/upcoming`, `/root-cause-explorer` and `/health` are new; the old routes remain reachable (the brief: keep them until the new ones are done — removing them is a later, explicit step). Each section loads and fails independently, shows ranges instead of single numbers where the backend provides them, and carries a SYNTHETIC notice; the what-if section always shows the brief's counterfactual sentence.

## D-056 (brief Phase 11) — The benchmark reads the test split only, puts baselines in the same table, and names its exclusions

`python -m app.evaluation.run_all` measures yield, risk, root cause, similarity, vision and system numbers. Tabular/root-cause/similarity numbers come from the chronological test split; vision uses the CNN's stratified held-out split because synthetic maps have no time axis (stated in the report). Temporal-model rows are read from their own artifact when it was evaluated on the same split size, and labelled so. Root-cause faults with no defensible candidate (`sensor_dropout`, `contamination_event`) are counted and excluded, as in Phase 3. There is no "accuracy" key anywhere in the output (test-pinned).

## D-057 — The shipped tabular artifacts came from a 1,500-lot test run; they were retrained on the 5,000-lot dataset without touching the shared database

`lot_yield_*_v2.0.0` metadata showed 225 test lots and 5 HPO trials — the conftest's 1,500-lot dataset — so a pytest run before the P6-5 fix had overwritten them. The models were retrained from `data/synthetic/` with `MONGODB_URI` pointed at an unreachable address, so training wrote artifacts (and MLflow runs) but performed **no** backfill into the shared Atlas cluster (D-014/D-036). Consequence: predictions stored in Atlas came from the older artifacts until someone re-runs `python scripts/train_yield_model.py` against it.
