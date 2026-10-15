"""
One command to build the entire synthetic dataset — and, optionally, the
whole bootstrap chain (Phase 6, P6-1; audit §8.5):

    cd backend
    .venv/Scripts/python.exe scripts/build_dataset.py             # generate only (default, unchanged)
    .venv/Scripts/python.exe scripts/build_dataset.py --all       # generate -> seed -> train -> evaluate
    .venv/Scripts/python.exe scripts/build_dataset.py --from seed # resume at a stage
    .venv/Scripts/python.exe scripts/build_dataset.py --stages train,evaluate
    .venv/Scripts/python.exe scripts/build_dataset.py --plan --all # print the resolved plan, run nothing

Default behavior (no flags) still generates only, exactly as before — the
README's step-by-step chain keeps working. `--all` delivers the audit's
promised one-command `generate→seed→train→evaluate`.

Stage semantics (honest operational behavior, P6-1):
  * generate — app.simulator writes data/synthetic/ (CSVs + Parquet + metadata).
  * seed     — scripts/seed_database.py: resumable, fingerprinted; requires a
               reachable MongoDB and fails loudly (non-zero exit) if not.
  * train    — scripts/train_yield_model.py: trains, records model_versions,
               backfills predictions/alerts. Degrades gracefully without Mongo
               (artifacts still land on disk; prints what to re-run).
  * evaluate — scripts/evaluate_yield_model.py: measures the on-disk bundles
               against the chronological test split -> evaluation_report.json.

Every stage prints its wall time; the first failure stops the chain with a
non-zero exit and names the exact `--from` resume point. Seeding touches a
database and training is slow, so nothing here runs implicitly: no flags
means generate only.
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import get_settings  # noqa: E402
from app.simulator.config import load_config  # noqa: E402
from app.simulator.generator import generate_dataset  # noqa: E402
from app.simulator.writer import write_dataset  # noqa: E402

# Ordered bootstrap chain (audit §8.5: generate -> seed -> train -> evaluate).
STAGES = ("generate", "seed", "train", "evaluate")


def build(n_lots: int | None = None, config_path: str | None = None) -> dict:
    cfg = load_config(Path(config_path) if config_path else None)
    if n_lots is not None:
        object.__setattr__(cfg, "n_lots", n_lots)  # frozen dataclass; CLI override only
    print(f"Generating {cfg.n_lots} lots (seed={cfg.seed}, schema v{cfg.schema_version})...")
    tables = generate_dataset(cfg)
    out_dir = write_dataset(cfg, tables)

    lots = tables["lots"]
    gt = tables["ground_truth"]
    low_yield_frac = float(lots["is_low_yield"].mean())
    fault_counts = gt["true_fault"].value_counts().to_dict()

    print(f"\nWrote dataset to {out_dir}")
    print(f"  lots={len(lots)} wafers={len(tables['wafers'])} defects={len(tables['defects'])} "
          f"telemetry_rows={len(tables['sensor_data'])} events={len(tables['events'])}")
    print(f"  low-yield fraction: {low_yield_frac:.3f} (target {cfg.low_yield_fraction})")
    print(f"  fault distribution: {fault_counts}")
    return {"tables": tables, "out_dir": out_dir, "low_yield_frac": low_yield_frac, "fault_counts": fault_counts}


# ---------------------------------------------------------------------------
# P6-1: stage machinery. The runners are looked up lazily so `--plan` (and the
# tests) never import the heavy training stack.
# ---------------------------------------------------------------------------
def _default_runners(n_lots: int | None, config_path: str | None) -> dict:
    """Stage name -> zero-arg callable, importing each script's main() lazily."""
    def run_generate() -> None:
        build(n_lots=n_lots, config_path=config_path)

    def run_seed() -> None:
        import seed_database
        seed_database.main()

    def run_train() -> None:
        import train_yield_model
        train_yield_model.main()

    def run_evaluate() -> None:
        import evaluate_yield_model
        evaluate_yield_model.main()

    return {
        "generate": run_generate,
        "seed": run_seed,
        "train": run_train,
        "evaluate": run_evaluate,
    }


def resolve_stages(stages: str | None = None, all_: bool = False, from_stage: str | None = None) -> list[str]:
    """Turn CLI intent into an ordered, validated stage list.

    Pure function (no imports, no I/O) so tests can pin the resolution rules:
    `--all` = the full chain, `--stages a,b` = exactly those in canonical
    order, `--from X` = the chain from X onward. Unknown stages are a
    ValueError, never silently dropped.
    """
    if all_ and stages:
        raise ValueError("use either --all or --stages, not both")

    if from_stage is not None and from_stage not in STAGES:
        raise ValueError(f"unknown --from stage {from_stage!r}; valid stages: {list(STAGES)}")

    if all_:
        selected = list(STAGES)
    elif stages:
        requested = [s.strip() for s in stages.split(",") if s.strip()]
        unknown = [s for s in requested if s not in STAGES]
        if unknown:
            raise ValueError(f"unknown stage(s) {unknown}; valid stages: {list(STAGES)}")
        # Preserve canonical chain order regardless of the order typed.
        selected = [s for s in STAGES if s in requested]
    elif from_stage is not None:
        # Bare `--from X` means "the chain from X onward", not "the
        # generate-only default minus X" (which would always be empty).
        selected = list(STAGES)
    else:
        selected = ["generate"]  # back-compatible default: generation only

    if from_stage is not None:
        start = STAGES.index(from_stage)
        selected = [s for s in selected if STAGES.index(s) >= start]

    return selected


def run_pipeline(stages: list[str], runners: dict | None = None, n_lots: int | None = None,
                 config_path: str | None = None) -> int:
    """Run stages in order; first failure stops the chain.

    Returns the process exit code: 0 all green, 1 on failure (with the exact
    `--from` resume point named). `runners` is injectable so tests exercise
    ordering/fail-stop semantics without training anything.
    """
    if runners is None:
        runners = _default_runners(n_lots, config_path)

    results: list[tuple[str, str, float]] = []
    for index, stage in enumerate(stages):
        print(f"\n=== [{index + 1}/{len(stages)}] {stage} ===")
        started = time.perf_counter()
        try:
            runners[stage]()
        except Exception as exc:  # noqa: BLE001 - the pipeline reports, it doesn't crash-traceback
            elapsed = time.perf_counter() - started
            resume = stages[index] if index < len(stages) else stages[-1]
            print(f"\n[Pipeline] stage '{stage}' FAILED after {elapsed:.1f}s: {exc}")
            print(f"[Pipeline] stages completed: {[s for s, _, _ in results] or 'none'}")
            print(f"[Pipeline] resume with: python scripts/build_dataset.py --from {resume}")
            return 1
        elapsed = time.perf_counter() - started
        results.append((stage, "ok", elapsed))
        print(f"=== [{index + 1}/{len(stages)}] {stage} ok ({elapsed:.1f}s) ===")

    print("\n[Pipeline] all stages complete:")
    for stage, _, elapsed in results:
        print(f"  {stage:10s} {elapsed:8.1f}s")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the synthetic dataset; with flags, run the full bootstrap chain."
    )
    parser.add_argument("--lots", type=int, default=None, help="Override number of lots (generate stage only).")
    parser.add_argument("--config", type=str, default=None, help="Path to an alternative simulator config.json.")
    parser.add_argument("--all", action="store_true", help="Run generate -> seed -> train -> evaluate.")
    parser.add_argument("--stages", type=str, default=None,
                        help=f"Comma-separated subset of {list(STAGES)} (canonical order preserved).")
    parser.add_argument("--from", dest="from_stage", type=str, default=None,
                        help="Resume the chain at this stage (inclusive).")
    parser.add_argument("--plan", action="store_true", help="Print the resolved stage list and exit (no side effects).")
    args = parser.parse_args()

    try:
        stages = resolve_stages(stages=args.stages, all_=args.all, from_stage=args.from_stage)
    except ValueError as exc:
        parser.error(str(exc))

    if args.plan:
        print(f"Resolved stages: {stages}")
        return

    note = "" if args.stages or args.all or args.from_stage else " (generation only — add --all for seed/train/evaluate)"
    print(f"Pipeline stages: {stages}{note}")
    raise SystemExit(run_pipeline(stages, n_lots=args.lots, config_path=args.config))


if __name__ == "__main__":
    main()
