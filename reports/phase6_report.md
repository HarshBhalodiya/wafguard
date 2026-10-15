# Phase 6 Report — One-command pipeline & operational tooling

> **SYNTHETIC DATA demo.** Nothing in this phase changes any model or metric; it changes
> how the existing pipeline is operated: one command, one preflight, one honest health
> check, and a test suite that can no longer destroy what it tests.

## What changed

| Was | Now |
|---|---|
| The audit's Phase-1 promise (*"one-command `python scripts/build_dataset.py` runs generate→seed→train→evaluate"*, `docs/AUDIT.md` §8.5) was never delivered; the documented path was an 8-command manual chain | `build_dataset.py` gained a stage machinery: `--all` runs generate→seed→train→evaluate, `--stages a,b` selects a subset (canonical order), `--from X` resumes inclusively, `--plan` prints the resolved plan. Default (no flags) still generates only, unchanged (P6-1) |
| `/api/health` reported only MongoDB reachability — "healthy" with no dataset or no trained models, while every prediction endpoint would 503 | Health now reports a **readiness block** (dataset fingerprint, tabular-model readiness, served model versions, evidence table & embeddings presence) and `status: degraded` with a fix hint when the DB is up but the app is not serviceable (P6-3) |
| No way to diagnose a broken setup in one command; error hints were scattered per-service | `scripts/doctor.py`: env / Mongo / dataset / artifacts / evidence / embeddings in one OK-WARN-FAIL table, with the exact fix command per line and exit 1 on any FAIL (P6-4) |
| A pytest session trained in-process models and `save_bundle()` wrote them **over the real `models/artifacts/`** (phase2 report known problem 2) | conftest points `model_dir` at a session tmp copy; verified by diffing artifact mtimes across a full suite run — the repo's artifacts are untouched (P6-5) |
| `docs/setup-guide.md` (read by the evaluation pipeline) referenced the superseded v1 generator, claimed 39/17 tests, and omitted RBAC and the one-command path | Rewritten: one-command chain + resumable variants, doctor, RBAC login note, honest test-suite description (P6-6) |

## P6-1 — The one-command pipeline

`backend/scripts/build_dataset.py`:

- **Stage registry** `("generate", "seed", "train", "evaluate")`; each stage maps to the
  existing script's `main()` (generate = in-process `build()`), imported lazily so `--plan`
  never loads the training stack.
- **`resolve_stages()` is a pure function** (no I/O) so the resolution rules are pinned by
  tests: default = generate only (back-compat), `--all` = full chain, `--stages` subset in
  canonical order regardless of typed order, bare `--from X` = the chain from X onward,
  unknown stages are a hard error (exit 2), never silently dropped.
- **`run_pipeline()`** times each stage, stops at the first failure with exit 1, and prints
  the exact `--from` resume command. Runners are injectable, so ordering/fail-stop are
  tested without training anything.
- A test caught a real bug during development: bare `--from evaluate` initially filtered
  the generate-only default down to the empty list; fixed so `--from` implies the chain.

## P6-3 — Honest health/readiness

- `app/ops/readiness.py`: `dataset_readiness()` (config fingerprint from
  `_generation_metadata.json`), `bundle_readiness()` (latest-pointer → artifact resolution),
  `collect_readiness()` (full detail, reused verbatim by the doctor), and
  `shallow_readiness()` — the **unauthenticated** `/api/health` view, deliberately limited to
  booleans, version strings and the fingerprint (a test asserts no filesystem path and no
  fix-command leaks into it).
- `status: healthy` now means *actually serviceable* (DB up **and** tabular bundles
  present); a DB-up-but-untrained deployment reports `degraded` with the command to run.

## P6-4 — Doctor

`backend/scripts/doctor.py` — 13 checks across env (`.env` loaded, JWT dev default, LLM
provider coherence, alert dispatch), Mongo (redacted URI, 3s timeout, index presence), and
data/artifacts (dataset fingerprint, all four bundle pointers, evidence table, both
embedding spaces). Live run on this checkout: **13 OK, 0 WARN, exit 0**. It reuses
`app.ops.readiness`, so the doctor, the health endpoint and the docs cannot drift apart.

## P6-5 — The test suite can no longer clobber production artifacts

`tests/conftest.py` copies the real artifacts into a session tmp dir once and redirects
`settings.model_dir` there (a copy, not an empty dir, so the vision/similarity tests keep
their CNN, evidence table and embeddings). Proof: full-suite artifact-mtime diff before/after
— `IDENTICAL`.

## Verification (P6-6)

- Backend: **140/140 pass** (`reports/phase6_testrun.log`, pytest exit 0; 15 new tests in
  `tests/test_phase6_ops.py`: stage resolution incl. the `--from` regression, runner
  fail-stop, the Phase-2-promised metadata smoke test — `GET /api/models` serves exactly
  `train_yield_model.MODEL_VERSION` — the health readiness contract, the shallow-view leak
  guard, and readiness primitives on tmp dirs).
- Frontend: **22/22** (unchanged this phase).
- Artifact isolation: `models/artifacts/` mtimes identical before/after the full run.
- Pipeline: `--plan` verified live for `--all`, subset, `--from`, and the unknown-stage
  error path (exit 2); stage runners verified by injected-fake tests.

## Known limitations / follow-ups

- The full `--all` chain was **not re-run end-to-end against the live Atlas cluster**:
  generate would overwrite the seeded 5,000-lot demo dataset, and reseeding a shared
  cluster was a Phase-1 user-approved one-off, not a standing authorization. What is
  verified: stage machinery, `--plan`, the evaluate-stage dependency chain, and the
  API smoke test. Running `--all` against a scratch Mongo + `SYNTHETIC_DATA_PATH` is the
  natural first command on a fresh machine — and exactly what `docs/setup-guide.md` now
  documents.
- Readiness is a point-in-time filesystem/DB check, not a live probe: it does not verify
  that a prediction can actually be computed end-to-end (that is the test suite's job).
- `doctor.py` checks index *presence* on three representative collections, not every
  index in `INDEX_SPECS`.
