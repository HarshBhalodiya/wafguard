"""
Phase 3 validation harness (P3-7): score the multi-signal root-cause engine's
candidate ranking against the generator's hidden labels on the chronological
TEST split, and report the SHAP+deviation baseline beside it.

Method (every number measured, none assumed):
  1. Rebuild the engineered features from data/synthetic/ and seed an
     in-memory mongomock DB with the same seeder the real path uses
     (defect docs skipped — unused by this harness).
  2. Backfill predictions with the trained bundles on disk (same code path
     as scripts/train_yield_model.py).
  3. Build the measured pattern-cause table on the TRAIN split (fresh, via
     app.evaluation) and inject it — identical content to what
     scripts/build_evidence_tables.py ships; if the artifact exists we also
     verify it matches.
  4. For every faulty lot in the TEST split: compute_root_cause() ranks the
     10 candidates; rank quality vs ground truth is scored Top-1/3/5 + MRR,
     per fault type, against fault->acceptable-candidate sets documented in
     app/evaluation/fault_mapping.py. Faults with no defensible mapping
     (sensor_dropout, contamination_event) are counted and reported, never
     silently folded into the metrics.
  5. Baseline = ranking by (shap_attribution + parameter_deviation) alone,
     i.e. the pre-upgrade core ranking, computed from the same signal
     breakdown (no second SHAP pass).

Usage:
    cd backend
    python scripts/evaluate_rootcause.py [--max-lots N]
"""
import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import get_settings  # noqa: E402
from app.evaluation.fault_mapping import UNMAPPABLE_FAULTS, acceptable_candidates  # noqa: E402
from app.evaluation.pattern_associations import build_pattern_cause_table  # noqa: E402
from app.ml.preprocessing import build_lot_features_bulk, chronological_split  # noqa: E402
from app.ml.yield_model import load_latest_bundle  # noqa: E402
from app.services import rootcause_service  # noqa: E402

REPORTS_DIR = Path(__file__).resolve().parent.parent.parent / "reports"


def _seed_mongomock(data_dir: Path):
    """In-memory DB seeded with the same code path production uses."""
    import mongomock
    import seed_database as sd

    client = mongomock.MongoClient()
    db = client["wafguard_eval"]
    # The root-cause engine never reads the defects collection; skip its bulk
    # insert by pre-marking it complete for this fresh DB (fingerprint matches).
    fingerprint = sd._dataset_fingerprint(data_dir)
    db.seed_state.replace_one(
        {"_id": "seed"},
        {"_id": "seed", "fingerprint": fingerprint, "completed": ["defects"]},
        upsert=True,
    )
    sd.seed_base_collections(db, data_dir)
    return db


def _load_trained_bundles(settings):
    model_dir = settings.resolve_path(settings.model_dir)
    reg = load_latest_bundle("lot_yield_regressor", model_dir)
    clf = load_latest_bundle("lot_yield_classifier", model_dir)
    if reg is None or clf is None:
        raise SystemExit("Trained bundles missing — run scripts/train_yield_model.py first.")
    return reg, clf


def _backfill(db, features_df: pd.DataFrame, reg_bundle: dict, clf_bundle: dict) -> None:
    """Mirror of train_yield_model.backfill_predictions for the keys this harness needs
    (avoids importing the training script's ModelBundle objects)."""
    from app.ml.preprocessing import NUMERIC_FEATURE_COLUMNS, single_row_to_model_matrix
    from app.services.risk_service import compute_historical_defect_risk, compute_risk
    from app.utils.time import utc_now

    db.predictions.delete_many({})
    features_sorted = features_df.sort_values("process_start").reset_index(drop=True)
    equipment_by_id = {
        e["equipment_id"]: e for e in db.equipment.find({}, {"_id": 0})
    }
    for _, row in features_sorted.iterrows():
        numeric_snapshot = {c: float(row[c]) for c in NUMERIC_FEATURE_COLUMNS}
        full_snapshot = {**numeric_snapshot, "equipment": row["equipment"], "process_step": row["process_step"]}
        X = single_row_to_model_matrix(full_snapshot, clf_bundle["feature_columns"])
        predicted_yield = round(float(reg_bundle["production"].predict(X)[0]), 2)
        model_probability = round(float(clf_bundle["production"].predict_proba(X)[0][1]), 4)

        prior = features_sorted[
            (features_sorted.equipment == row["equipment"]) & (features_sorted.process_start < row["process_start"])
        ].tail(10)
        historical_defect_risk = compute_historical_defect_risk(prior["yield"].tolist())
        risk = compute_risk(model_probability, full_snapshot, equipment_by_id.get(row["equipment"]), historical_defect_risk)

        db.lots.update_one(
            {"lot_id": row["lot_id"]},
            {"$set": {"predicted_yield": predicted_yield, "risk_probability": risk["risk_probability"], "risk_level": risk["risk_level"]}},
        )
        db.predictions.insert_one(
            {
                "lot_id": row["lot_id"],
                "model_name": "lot_yield_classifier",
                "model_version": clf_bundle.get("version", ""),
                "predicted_yield": predicted_yield,
                "risk_probability": risk["risk_probability"],
                "risk_level": risk["risk_level"],
                "features_snapshot": {**full_snapshot, "model_probability": model_probability},
                "risk_breakdown": risk["breakdown"],
                "timestamp": utc_now(),
            }
        )


def _rank_metrics(ranking: list[str], acceptable: frozenset[str]) -> dict:
    rank = None
    for i, name in enumerate(ranking, start=1):
        if name in acceptable:
            rank = i
            break
    return {
        "rank": rank,
        "hit@1": rank is not None and rank <= 1,
        "hit@3": rank is not None and rank <= 3,
        "hit@5": rank is not None and rank <= 5,
        "reciprocal_rank": (1.0 / rank) if rank else 0.0,
    }


def _aggregate(rows: list[dict], key: str) -> dict:
    n = len(rows)
    if n == 0:
        return {"n": 0}
    return {
        "n": n,
        "top1": round(sum(r[key]["hit@1"] for r in rows) / n, 4),
        "top3": round(sum(r[key]["hit@3"] for r in rows) / n, 4),
        "top5": round(sum(r[key]["hit@5"] for r in rows) / n, 4),
        "mrr": round(sum(r[key]["reciprocal_rank"] for r in rows) / n, 4),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-lots", type=int, default=0, help="cap faulty test lots (0 = all)")
    args = parser.parse_args()

    settings = get_settings()
    data_dir = settings.resolve_path(settings.synthetic_data_path)

    import train_yield_model as tym

    print("[1/5] loading synthetic tables + features...")
    lots_df, process_df, sensor_df, maintenance_df = tym.load_synthetic_tables(settings)
    features_df = build_lot_features_bulk(lots_df, process_df, sensor_df, maintenance_df)
    _, _, test_df = chronological_split(features_df.copy())
    test_ids = set(test_df["lot_id"])

    print("[2/5] seeding in-memory DB + backfilling predictions from trained bundles...")
    t0 = time.perf_counter()
    db = _seed_mongomock(data_dir)
    reg_bundle, clf_bundle = _load_trained_bundles(settings)
    _backfill(db, features_df, reg_bundle, clf_bundle)
    print(f"      seeded+backfilled in {time.perf_counter() - t0:.1f}s ({db.lots.count_documents({})} lots)")

    print("[3/5] building measured pattern-cause table on the TRAIN split...")
    ground_truth = pd.read_parquet(data_dir / "ground_truth.parquet")
    table = build_pattern_cause_table(lots_df[["lot_id", "process_start"]], ground_truth)
    rootcause_service.set_pattern_cause_table(table)
    artifact_path = rootcause_service._pattern_table_path()
    artifact_match = None
    if artifact_path.exists():
        with open(artifact_path, encoding="utf-8") as f:
            shipped = json.load(f)
        artifact_match = shipped.get("associations") == table.get("associations")
    print(f"      {len(table['associations'])} associations; shipped artifact matches fresh build: {artifact_match}")

    print("[4/5] ranking candidates for faulty TEST-split lots...")
    gt = ground_truth.set_index("lot_id")
    faulty_ids = [lid for lid in test_ids if gt.loc[lid, "true_fault"] != "none"]
    faulty_ids.sort()
    if args.max_lots:
        faulty_ids = faulty_ids[: args.max_lots]

    rows: list[dict] = []
    unmappable: dict[str, int] = {}
    latencies: list[float] = []
    for i, lot_id in enumerate(faulty_ids, start=1):
        fault = str(gt.loc[lot_id, "true_fault"])
        acceptable = acceptable_candidates(fault)
        if not acceptable:
            unmappable[fault] = unmappable.get(fault, 0) + 1
            continue
        t0 = time.perf_counter()
        result = rootcause_service.compute_root_cause(db, str(lot_id), top_n=len(rootcause_service.CANDIDATE_DEFINITIONS))
        latencies.append((time.perf_counter() - t0) * 1000)

        new_ranking = [c["candidate"] for c in result["candidates"]]
        baseline_ranking = sorted(
            result["candidates"],
            key=lambda c: c["signal_breakdown"]["shap_attribution"] + c["signal_breakdown"]["parameter_deviation"],
            reverse=True,
        )
        rows.append(
            {
                "lot_id": str(lot_id),
                "fault": fault,
                "new": _rank_metrics(new_ranking, acceptable),
                "baseline": _rank_metrics([c["candidate"] for c in baseline_ranking], acceptable),
                "top_candidate": new_ranking[0],
                "pattern": result.get("dominant_wafer_pattern"),
            }
        )
        if i % 50 == 0:
            print(f"      {i}/{len(faulty_ids)} lots evaluated...")

    print("[5/5] aggregating...")
    by_fault: dict[str, dict] = {}
    for fault in sorted({r["fault"] for r in rows}):
        frows = [r for r in rows if r["fault"] == fault]
        by_fault[fault] = {"new": _aggregate(frows, "new"), "baseline": _aggregate(frows, "baseline")}

    metrics = {
        "engine": rootcause_service.MODEL_NAME,
        "pattern_table_version": table.get("table_version"),
        "pattern_table_artifact_matches_fresh_build": artifact_match,
        "split": "chronological test split (last 15% of lots by process_start)",
        "n_faulty_test_lots": len(faulty_ids),
        "n_scored": len(rows),
        "n_unmappable": unmappable,
        "overall": {"new": _aggregate(rows, "new"), "baseline": _aggregate(rows, "baseline")},
        "per_fault": by_fault,
        "latency_ms": {
            "p50": round(sorted(latencies)[len(latencies) // 2], 1) if latencies else None,
            "p95": round(sorted(latencies)[int(len(latencies) * 0.95)], 1) if latencies else None,
            "mean": round(sum(latencies) / len(latencies), 1) if latencies else None,
        },
        "baseline_definition": "rank by (shap_attribution + parameter_deviation) — the pre-upgrade core ranking",
        "note": "SYNTHETIC data; 'acceptable candidates' per fault are documented in app/evaluation/fault_mapping.py.",
    }

    REPORTS_DIR.mkdir(exist_ok=True)
    metrics_path = REPORTS_DIR / "phase3_rootcause_metrics.json"
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)
    _write_markdown(metrics, REPORTS_DIR / "phase3_report.md")

    print(f"\nMetrics -> {metrics_path}")
    print(f"Report  -> {REPORTS_DIR / 'phase3_report.md'}")
    o = metrics["overall"]
    print(f"\nOverall (n={o['new']['n']}): new engine Top1 {o['new']['top1']} / Top3 {o['new']['top3']} / "
          f"Top5 {o['new']['top5']} / MRR {o['new']['mrr']}")
    print(f"           baseline        Top1 {o['baseline']['top1']} / Top3 {o['baseline']['top3']} / "
          f"Top5 {o['baseline']['top5']} / MRR {o['baseline']['mrr']}")
    if unmappable:
        print(f"Unmappable faults excluded from metrics: {unmappable}")


def _write_markdown(metrics: dict, path: Path) -> None:
    o = metrics["overall"]
    lines = [
        "# Phase 3 Report — Root-Cause Intelligence (P2)",
        "",
        "> **SYNTHETIC DATA.** Every number below is measured on the simulated 5,000-lot",
        "> dataset (generator seed 20260930) against its hidden labels. None of this is",
        "> real-fab accuracy. Root-cause output remains **ranked candidates with evidence**",
        "> — correlation, not proof of cause.",
        "",
        "## What changed (vs the pre-upgrade engine)",
        "",
        "| Was | Now |",
        "|---|---|",
        "| Authored `WAFER_PATTERN_CANDIDATE_BOOST` dict (e.g. Edge-Ring → RF 0.70) | Measured pattern→cause table built on the train split, with counts + smoothing + source citation (P3-1) |",
        "| `anomaly_status` text-match (0.9/0.4/0.1 authored constants) | Measured excursion/failure/maintenance/low-yield rates from events+maintenance+lots, shrunk to the fab baseline for low-N tools, sample sizes in evidence (P3-2) |",
        "| SHAP groups ignored Phase 2A extended columns | slope / excursions / time-outside join each candidate's SHAP group and appear as evidence (P3-3) |",
        "| Temporal signal = last-minus-first drift | Per-channel shape: OLS ramp + end-drift + time-outside, described in the evidence label (P3-4) |",
        "| Evidence had no ids; recommendations were orphans | Stable `evidence_id`s; every recommendation carries `grounded_in` (P3-6) |",
        "| Parameter kNN only | + cosine search over temporal/vision embeddings at `/api/similarity/{lot_id}` (P3-5) |",
        "",
        "## Evaluation method (P3-7)",
        "",
        f"- Engine: `{metrics['engine']}`, pattern table v{metrics['pattern_table_version']},",
        f"  shipped artifact matches fresh train-split build: **{metrics['pattern_table_artifact_matches_fresh_build']}**.",
        f"- Split: {metrics['split']}. Scored **{metrics['n_scored']}** faulty lots",
        f"  (unmappable, excluded from metrics: {metrics['n_unmappable'] or 'none'}).",
        "- Fault → acceptable candidates: documented per fault in `app/evaluation/fault_mapping.py`;",
        "  multi-mapped faults get fractional credit in the table builder.",
        f"- Baseline: {metrics['baseline_definition']}.",
        "",
        "## Results — new engine vs baseline",
        "",
        "### Overall",
        "",
        "| Engine | n | Top-1 | Top-3 | Top-5 | MRR |",
        "|---|---|---|---|---|---|",
        f"| **new (multi-signal)** | {o['new'].get('n', 0)} | {o['new'].get('top1', '—')} | "
        f"{o['new'].get('top3', '—')} | {o['new'].get('top5', '—')} | {o['new'].get('mrr', '—')} |",
        f"| baseline (SHAP+deviation) | {o['baseline'].get('n', 0)} | {o['baseline'].get('top1', '—')} | "
        f"{o['baseline'].get('top3', '—')} | {o['baseline'].get('top5', '—')} | {o['baseline'].get('mrr', '—')} |",
        "",
        "### Per fault type",
        "",
        "| Fault | n | Engine | Top-1 | Top-3 | Top-5 | MRR |",
        "|---|---|---|---|---|---|---|",
    ]
    for fault, agg in metrics["per_fault"].items():
        n = agg["new"]["n"]
        for engine in ("new", "baseline"):
            d = agg[engine]
            label = "**new**" if engine == "new" else "baseline"
            lines.append(
                f"| {fault if engine == 'new' else ''} | {n if engine == 'new' else ''} | {label} | "
                f"{d['top1']} | {d['top3']} | {d['top5']} | {d['mrr']} |"
            )
    lat = metrics["latency_ms"]
    lines += [
        "",
        "## Engine latency (per call, in-memory DB)",
        "",
        f"- p50 **{lat['p50']} ms**, p95 **{lat['p95']} ms**, mean {lat['mean']} ms",
        "",
        "## Honest failures / limitations",
        "",
        "- `sensor_dropout` and `contamination_event` lots have **no defensible candidate** in the",
        "  current 10-candidate set — they are counted above and excluded from the metrics rather",
        "  than being scored leniently. The candidate set would need sensor-health / contamination",
        "  candidates to cover them (candidate-set change, not done silently).",
        "- The measured Edge-Ring association is genuinely ambiguous in the generator (both",
        "  rf_power_drift and chamber_pressure_instability paint Edge-Ring) — the table shows the",
        "  ambiguity instead of the old dict pretending to disambiguate.",
        "- The pattern table is measured on the train split from SYNTHETIC labels; it stands in for",
        "  historical failure-analysis records. It is not read by any training code",
        "  (tests/test_ground_truth_leakage.py enforces this).",
        "",
        "See `reports/phase3_rootcause_metrics.json` for full machine-readable metrics.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
