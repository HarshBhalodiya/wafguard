"""
WafGuard evaluation benchmark (Phase 11, brief P10).

    cd backend
    python -m app.evaluation.run_all                    # everything (root cause takes ~5 min)
    python -m app.evaluation.run_all --skip-rootcause   # fast pass

Writes reports/benchmark.md and reports/benchmark.json. Rules it follows:

  * Every tabular / root-cause / similarity number is measured on the held-out
    FUTURE test split: the chronologically last 15% of lots (the same
    app.ml.preprocessing.chronological_split the models were trained with).
    Train/val lots are only ever the history a model or a search may use.
  * Every result is reported next to its baselines, in the same table.
  * No single "accuracy" number: each task gets the metrics the brief names.
  * Vision has no time axis (procedurally generated maps), so it uses the
    stratified held-out test split the CNN was trained against; this is said
    in the report.
  * This module lives in app/evaluation, the ONLY package allowed to read
    ground_truth.parquet (tests/test_ground_truth_leakage.py).

ALL DATA IS SYNTHETIC — the numbers describe behaviour on the simulator, not a fab.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    precision_score,
    r2_score,
    recall_score,
    roc_auc_score,
)

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPORTS_DIR = BACKEND_DIR.parent / "reports"
SCRIPTS_DIR = BACKEND_DIR / "scripts"
for _p in (BACKEND_DIR, SCRIPTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from app.config import LOW_YIELD_THRESHOLD, PROCESS_PARAMETER_RANGES, get_settings  # noqa: E402
from app.ml.baselines import fit_classifier_baselines, fit_regressor_baselines  # noqa: E402
from app.ml.preprocessing import chronological_split, single_row_to_model_matrix, to_model_matrix  # noqa: E402
from app.ml.yield_model import load_latest_bundle  # noqa: E402
from app.ops.tracking import dataset_version, log_run  # noqa: E402

SEED = 20261012
K_NEIGHBORS = 5
ECE_BINS = 10


# =============================================================================== helpers
def _r(x, digits: int = 4):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else round(float(x), digits)


def expected_calibration_error(y: np.ndarray, p: np.ndarray, n_bins: int = ECE_BINS) -> float:
    bins = np.minimum((p * n_bins).astype(int), n_bins - 1)
    return float(sum((bins == b).mean() * abs(p[bins == b].mean() - y[bins == b].mean()) for b in range(n_bins) if (bins == b).any()))


def _percentiles(samples_ms: list[float]) -> dict:
    arr = np.asarray(samples_ms)
    return {"p50_ms": _r(np.percentile(arr, 50), 3), "p95_ms": _r(np.percentile(arr, 95), 3), "n_calls": int(len(arr))}


def _rss_mb() -> float | None:
    """Resident memory of this process (psutil if present, else OS-specific fallbacks)."""
    try:
        import psutil

        return psutil.Process(os.getpid()).memory_info().rss / 2**20
    except ImportError:
        pass
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]

        counters = PMC()
        counters.cb = ctypes.sizeof(PMC)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return counters.WorkingSetSize / 2**20
        return None
    try:
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    except ImportError:
        return None


# =============================================================================== context
def load_context(settings) -> dict:
    model_dir = settings.resolve_path(settings.model_dir)
    data_dir = settings.resolve_path(settings.synthetic_data_path)
    features = pd.read_parquet(model_dir / "lot_features_snapshot.parquet").sort_values("process_start").reset_index(drop=True)
    reg = load_latest_bundle("lot_yield_regressor", model_dir)
    clf = load_latest_bundle("lot_yield_classifier", model_dir)
    if reg is None or clf is None:
        raise SystemExit("Trained bundles missing — run scripts/train_yield_model.py first.")
    train, val, test = chronological_split(features.copy())
    columns = reg["feature_columns"]
    trained_n_test = (reg.get("metadata") or {}).get("metrics", {}).get("production", {}).get("n_test_samples")
    if trained_n_test is not None and trained_n_test != len(test):
        raise SystemExit(
            f"The feature snapshot has {len(test)} test lots but the regressor was evaluated on {trained_n_test}: "
            "artifacts come from different training runs. Re-run scripts/train_yield_model.py."
        )
    return {
        "settings": settings,
        "model_dir": model_dir,
        "data_dir": data_dir,
        "features": features,
        "train": train,
        "val": val,
        "test": test,
        "reg": reg,
        "clf": clf,
        "X_train": to_model_matrix(train, known_dummy_columns=columns)[0],
        "X_val": to_model_matrix(val, known_dummy_columns=columns)[0],
        "X_test": to_model_matrix(test, known_dummy_columns=columns)[0],
        "ground_truth": pd.read_parquet(data_dir / "ground_truth.parquet"),
    }


def split_info(ctx: dict) -> dict:
    return {
        "method": "chronological 70/15/15 by process_start (test = the most recent 15% of lots)",
        "n_train": len(ctx["train"]),
        "n_val": len(ctx["val"]),
        "n_test": len(ctx["test"]),
        "test_start": str(ctx["test"]["process_start"].min()),
        "test_end": str(ctx["test"]["process_start"].max()),
        "train_end": str(ctx["train"]["process_start"].max()),
        "test_low_yield_rate": _r(ctx["test"]["is_low_yield"].mean()),
    }


# =============================================================================== yield
def _regression_metrics(y, pred) -> dict:
    return {"mae": _r(mean_absolute_error(y, pred)), "rmse": _r(np.sqrt(mean_squared_error(y, pred))), "r2": _r(r2_score(y, pred))}


def eval_yield(ctx: dict) -> dict:
    y_train, y_test = ctx["train"]["yield"].to_numpy(), ctx["test"]["yield"].to_numpy()
    rows = {"xgboost (production)": _regression_metrics(y_test, ctx["reg"]["production"].predict(ctx["X_test"]))}
    rows["random forest (baseline)"] = _regression_metrics(y_test, ctx["reg"]["baseline"].predict(ctx["X_test"]))
    for name, model in fit_regressor_baselines(ctx["X_train"], y_train).items():
        rows[name.replace("_", " ") + " (baseline)"] = _regression_metrics(y_test, model.predict(ctx["X_test"]))
    temporal = _temporal_metrics(ctx)
    for key, label in (("gru_yield", "GRU temporal model"), ("transformer_yield", "temporal transformer")):
        if temporal.get(key):
            m = temporal[key]
            rows[f"{label} (from its artifact)"] = {"mae": _r(m.get("mae")), "rmse": _r(m.get("rmse")), "r2": _r(m.get("r2"))}
    return {"target": "lot yield (%)", "n_test": int(len(y_test)), "models": rows}


def _temporal_metrics(ctx: dict) -> dict:
    path = ctx["model_dir"] / "temporal_metrics.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if data.get("n_test") == len(ctx["test"]) else {}


# =============================================================================== risk
def _classification_metrics(y: np.ndarray, score: np.ndarray, threshold: float) -> dict:
    flagged = score >= threshold
    tp, fp = int((flagged & (y == 1)).sum()), int((flagged & (y == 0)).sum())
    fn, tn = int((~flagged & (y == 1)).sum()), int((~flagged & (y == 0)).sum())
    is_prob = bool(np.all((score >= 0) & (score <= 1)))
    return {
        "threshold": _r(threshold),
        "precision": _r(precision_score(y, flagged, zero_division=0)),
        "recall": _r(recall_score(y, flagged, zero_division=0)),
        "f1": _r(f1_score(y, flagged, zero_division=0)),
        "pr_auc": _r(average_precision_score(y, score)) if len(np.unique(score)) > 1 else _r(y.mean()),
        "roc_auc": _r(roc_auc_score(y, score)) if len(np.unique(score)) > 1 else 0.5,
        "calibration_ece": _r(expected_calibration_error(y, score)) if is_prob else None,
        "false_alarm_rate": _r(fp / (fp + tn)) if (fp + tn) else None,
        "false_discovery_rate": _r(fp / (fp + tp)) if (fp + tp) else None,
        "missed_low_yield": fn,
        "n_flagged": int(flagged.sum()),
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
    }


def spec_band_rule(test: pd.DataFrame) -> np.ndarray:
    """Engineering baseline: flag a lot when any tracked channel's in-run max or mean
    left its spec band (what single-parameter SPC limits would catch)."""
    flagged = np.zeros(len(test), dtype=bool)
    for name, (lo, hi) in PROCESS_PARAMETER_RANGES.items():
        for suffix in ("_max", "_mean"):
            col = f"{name}{suffix}"
            if col in test.columns:
                flagged |= ((test[col] < lo) | (test[col] > hi)).to_numpy()
    return flagged.astype(float)


def eval_risk(ctx: dict) -> dict:
    y_train, y_test = ctx["train"]["is_low_yield"].to_numpy(), ctx["test"]["is_low_yield"].to_numpy()
    clf = ctx["clf"]
    calibration = (clf.get("metadata") or {}).get("metrics", {}).get("calibration") or {}
    threshold = float(calibration.get("decision_threshold") or 0.5)
    production = clf["production"]
    rows = {"xgboost calibrated (production)": _classification_metrics(y_test, production.predict_proba(ctx["X_test"])[:, 1], threshold)}
    inner = production.calibrated_classifiers_[0].estimator if hasattr(production, "calibrated_classifiers_") else None
    if inner is not None and hasattr(inner, "estimator"):
        inner = inner.estimator
    if inner is not None:
        rows["xgboost before calibration"] = _classification_metrics(y_test, inner.predict_proba(ctx["X_test"])[:, 1], 0.5)
    rows["random forest (baseline)"] = _classification_metrics(y_test, clf["baseline"].predict_proba(ctx["X_test"])[:, 1], 0.5)
    baselines = fit_classifier_baselines(ctx["X_train"], y_train)
    rows["logistic regression (baseline)"] = _classification_metrics(y_test, baselines["logreg"].predict_proba(ctx["X_test"])[:, 1], 0.5)
    rows["base rate (baseline)"] = _classification_metrics(y_test, np.full(len(y_test), float(y_train.mean())), 0.5)
    rows["spec-band rule (engineering baseline)"] = _classification_metrics(y_test, spec_band_rule(ctx["test"]), 0.5)
    temporal = _temporal_metrics(ctx)
    for key, label in (("gru_risk", "GRU temporal model"), ("transformer_risk", "temporal transformer")):
        if temporal.get(key):
            m = temporal[key]
            rows[f"{label} (from its artifact)"] = {k: _r(m.get(k)) for k in ("precision", "recall", "f1", "pr_auc", "roc_auc")}
    return {
        "target": f"low yield (< {LOW_YIELD_THRESHOLD}%)",
        "n_test": int(len(y_test)),
        "n_low_yield": int(y_test.sum()),
        "production_threshold_source": "chosen on the validation split at training time",
        "definitions": {
            "false_alarm_rate": "FP / (FP + TN): share of healthy lots that were flagged",
            "false_discovery_rate": "FP / (FP + TP): share of flags that were wrong",
            "calibration_ece": f"expected calibration error, {ECE_BINS} equal-width bins",
        },
        "models": rows,
    }


# =============================================================================== root cause
def eval_rootcause(ctx: dict, max_lots: int = 0) -> dict:
    import evaluate_rootcause as harness

    from app.evaluation.fault_mapping import FAULT_TO_CANDIDATES, acceptable_candidates
    from app.evaluation.pattern_associations import build_pattern_cause_table
    from app.services import rootcause_service

    t0 = time.perf_counter()
    db = harness._seed_mongomock(ctx["data_dir"])
    harness._backfill(db, ctx["features"], ctx["reg"], ctx["clf"])
    lots = pd.read_csv(ctx["data_dir"] / "lots.csv", usecols=["lot_id", "process_start"])
    gt = ctx["ground_truth"]
    rootcause_service.set_pattern_cause_table(build_pattern_cause_table(lots, gt))
    seed_seconds = time.perf_counter() - t0

    gt_idx = gt.set_index("lot_id")
    train_ids = set(ctx["train"]["lot_id"])
    prior_counts: dict[str, float] = {}
    for fault in gt_idx.loc[gt_idx.index.isin(train_ids), "true_fault"]:
        cands = FAULT_TO_CANDIDATES.get(fault, frozenset())
        for c in cands:
            prior_counts[c] = prior_counts.get(c, 0.0) + 1.0 / len(cands)

    test_ids = sorted(lid for lid in ctx["test"]["lot_id"] if gt_idx.loc[lid, "true_fault"] != "none")
    if max_lots:
        test_ids = test_ids[:max_lots]
    rows, unmappable, latencies = [], {}, []
    rng = np.random.default_rng(SEED)
    for lot_id in test_ids:
        fault = str(gt_idx.loc[lot_id, "true_fault"])
        acceptable = acceptable_candidates(fault)
        if not acceptable:
            unmappable[fault] = unmappable.get(fault, 0) + 1
            continue
        start = time.perf_counter()
        result = rootcause_service.compute_root_cause(db, lot_id, top_n=len(rootcause_service.CANDIDATE_DEFINITIONS))
        latencies.append((time.perf_counter() - start) * 1000)
        cands = result["candidates"]
        names = [c["candidate"] for c in cands]
        old = sorted(cands, key=lambda c: c["signal_breakdown"]["shap_attribution"] + c["signal_breakdown"]["parameter_deviation"], reverse=True)
        rows.append(
            {
                "lot_id": lot_id,
                "fault": fault,
                "multi_signal_engine": harness._rank_metrics(names, acceptable),
                "shap_plus_deviation": harness._rank_metrics([c["candidate"] for c in old], acceptable),
                "train_prior_frequency": harness._rank_metrics(sorted(names, key=lambda n: -prior_counts.get(n, 0.0)), acceptable),
                "random_order": harness._rank_metrics(list(rng.permutation(names)), acceptable),
            }
        )
    rootcause_service.reset_pattern_cause_table()
    engines = ("multi_signal_engine", "shap_plus_deviation", "train_prior_frequency", "random_order")
    per_fault = {
        fault: {e: harness._aggregate([r for r in rows if r["fault"] == fault], e) for e in engines}
        for fault in sorted({r["fault"] for r in rows})
    }
    return {
        "split": "faulty lots in the chronological test split, scored against hidden ground truth",
        "n_faulty_test_lots": len(test_ids),
        "n_scored": len(rows),
        "unmappable_excluded": unmappable,
        "acceptable_candidates": "app/evaluation/fault_mapping.py",
        "overall": {e: harness._aggregate(rows, e) for e in engines},
        "per_fault": per_fault,
        "latency": _percentiles(latencies) if latencies else None,
        "seed_and_backfill_seconds": _r(seed_seconds, 1),
        "baseline_definitions": {
            "shap_plus_deviation": "the pre-upgrade ranking: SHAP attribution + parameter deviation only",
            "train_prior_frequency": "rank candidates by how often they were the right answer on TRAIN-split faults",
            "random_order": f"uniformly random ranking (seed {SEED})",
        },
    }


# =============================================================================== similarity
def _space_vectors(ctx: dict, space: str) -> pd.DataFrame | None:
    from app.services.similarity_service import SIMILARITY_FEATURE_COLUMNS

    if space == "parameter":
        return ctx["features"].set_index("lot_id")[SIMILARITY_FEATURE_COLUMNS].astype(float)
    path = ctx["model_dir"] / "temporal_embeddings.parquet"
    if space == "temporal" and path.exists():
        return pd.read_parquet(path).set_index("lot_id")
    return None


def eval_similarity(ctx: dict) -> dict:
    """Query = each test lot; pool = train+val lots on the same process step (history only)."""
    from sklearn.preprocessing import StandardScaler

    gt = ctx["ground_truth"].set_index("lot_id")["true_fault"]
    meta = ctx["features"].set_index("lot_id")[["process_step", "is_low_yield"]]
    history_ids = list(ctx["train"]["lot_id"]) + list(ctx["val"]["lot_id"])
    test_ids = list(ctx["test"]["lot_id"])
    rng = np.random.default_rng(SEED)
    results = {}
    for space in ("parameter", "temporal"):
        vectors = _space_vectors(ctx, space)
        if vectors is None:
            results[space] = {"available": False}
            continue
        hist = vectors.loc[vectors.index.intersection(history_ids)]
        scaler = StandardScaler().fit(hist.to_numpy()) if space == "parameter" else None

        def embed(frame: pd.DataFrame) -> np.ndarray:
            arr = frame.to_numpy(dtype=float)
            if scaler is not None:
                return scaler.transform(arr)
            norms = np.linalg.norm(arr, axis=1, keepdims=True)
            return arr / np.where(norms == 0, 1, norms)

        hist_vec = embed(hist)
        hist_index = list(hist.index)
        hist_step = meta.loc[hist_index, "process_step"].to_numpy()
        stats = {"model": [], "random": []}
        for lot_id in test_ids:
            if lot_id not in vectors.index:
                continue
            step = meta.loc[lot_id, "process_step"]
            mask = hist_step == step
            if mask.sum() < K_NEIGHBORS:
                continue
            q = embed(vectors.loc[[lot_id]])[0]
            cand = np.where(mask)[0]
            if space == "parameter":
                dist = np.linalg.norm(hist_vec[cand] - q, axis=1)
            else:
                dist = -(hist_vec[cand] @ q)
            chosen = {"model": cand[np.argsort(dist, kind="stable")[:K_NEIGHBORS]], "random": rng.choice(cand, K_NEIGHBORS, replace=False)}
            for name, idx in chosen.items():
                ids = [hist_index[i] for i in idx]
                same_outcome = np.mean([meta.loc[i, "is_low_yield"] == meta.loc[lot_id, "is_low_yield"] for i in ids])
                fault = gt.loc[lot_id]
                same_fault = np.mean([gt.loc[i] == fault for i in ids]) if fault != "none" else None
                stats[name].append((same_outcome, same_fault, meta.loc[lot_id, "is_low_yield"], np.mean([meta.loc[i, "is_low_yield"] for i in ids])))
        results[space] = {"available": True, **{name: _similarity_summary(rows) for name, rows in stats.items()}}
    return {
        "k": K_NEIGHBORS,
        "query": "every test-split lot",
        "pool": "train+val lots on the same process step (history only)",
        "definitions": {
            "fault_retrieval_precision_at_k": "faulty queries only: share of the k neighbours with the SAME hidden fault type",
            "outcome_consistency_at_k": "share of the k neighbours whose low-yield status matches the query's",
            "low_yield_neighbour_rate_gap": "mean low-yield share among neighbours of low-yield queries minus that of healthy queries",
        },
        "spaces": results,
    }


def _similarity_summary(rows: list[tuple]) -> dict:
    if not rows:
        return {"n_queries": 0}
    outcome = [r[0] for r in rows]
    fault = [r[1] for r in rows if r[1] is not None]
    low_q = [r[3] for r in rows if r[2] == 1]
    ok_q = [r[3] for r in rows if r[2] == 0]
    return {
        "n_queries": len(rows),
        "outcome_consistency_at_k": _r(np.mean(outcome)),
        "fault_retrieval_precision_at_k": _r(np.mean(fault)) if fault else None,
        "n_faulty_queries": len(fault),
        "low_yield_neighbour_rate_gap": _r(np.mean(low_q) - np.mean(ok_q)) if low_q and ok_q else None,
    }


# =============================================================================== vision
def eval_vision(ctx: dict) -> dict:
    from sklearn.linear_model import LogisticRegression

    from app.config import WAFER_PATTERN_CLASSES
    from app.ml.wafer_model import load_latest_wafer_model, predict_batch
    import train_wafer_model as twm

    bundle = load_latest_wafer_model("wafer_pattern_cnn", ctx["model_dir"])
    if bundle is None:
        return {"available": False, "reason": "wafer_pattern_cnn artifact missing (scripts/train_wafer_model.py)"}
    X, y = twm.build_dataset()
    X_train, _X_val, X_test, y_train, _y_val, y_test = twm.stratified_split(X, y)
    n_classes = len(WAFER_PATTERN_CLASSES)

    def report(pred) -> dict:
        recall = recall_score(y_test, pred, labels=range(n_classes), average=None, zero_division=0)
        return {
            "macro_f1": _r(f1_score(y_test, pred, average="macro", zero_division=0)),
            "per_class_recall": {WAFER_PATTERN_CLASSES[i]: _r(recall[i]) for i in range(n_classes)},
            "confusion_matrix": confusion_matrix(y_test, pred, labels=list(range(n_classes))).tolist(),
        }

    cnn_pred, _ = predict_batch(bundle["model"], X_test)
    flat = lambda a: a.reshape(len(a), -1)[:, ::4]  # noqa: E731 - every 4th pixel keeps the baseline cheap
    logreg = LogisticRegression(max_iter=300, random_state=0).fit(flat(X_train), y_train)
    majority = np.full(len(y_test), np.bincount(y_train).argmax())
    return {
        "available": True,
        "warning": "Trained AND tested on procedurally generated SYNTHETIC wafer maps only (WM-811K absent). "
        "Expect a large drop on real maps, especially for rare classes.",
        "split": "stratified 60/20/20, seed 99 (maps have no time axis); test split only",
        "n_test": int(len(y_test)),
        "classes": list(WAFER_PATTERN_CLASSES),
        "models": {
            f"wafer CNN v{bundle['version']} (production)": report(cnn_pred),
            "logistic regression on pixels (baseline)": report(logreg.predict(flat(X_test))),
            "majority class (baseline)": report(majority),
        },
        "comparison_note": "CNN vs ResNet vs ViT experiment: reports/vision_comparison.md",
    }


# =============================================================================== system
def eval_system(ctx: dict, vision_available: bool) -> dict:
    reg, clf = ctx["reg"]["production"], ctx["clf"]["production"]
    test = ctx["test"]
    columns = ctx["reg"]["feature_columns"]
    from app.ml.preprocessing import NUMERIC_FEATURE_COLUMNS

    snapshots = [
        {**{c: float(row[c]) for c in NUMERIC_FEATURE_COLUMNS}, "equipment": row["equipment"], "process_step": row["process_step"]}
        for _, row in test.head(200).iterrows()
    ]
    for snap in snapshots[:5]:  # warm-up
        X = single_row_to_model_matrix(snap, columns)
        reg.predict(X), clf.predict_proba(X)
    single = []
    for snap in snapshots:
        start = time.perf_counter()
        X = single_row_to_model_matrix(snap, columns)
        reg.predict(X)
        clf.predict_proba(X)
        single.append((time.perf_counter() - start) * 1000)

    start = time.perf_counter()
    reg.predict(ctx["X_test"])
    clf.predict_proba(ctx["X_test"])
    batch_s = time.perf_counter() - start

    out = {
        "hardware_note": f"{os.cpu_count()} logical CPUs, CPU-only inference, single process; not a load test",
        "tabular_single_lot_predict": _percentiles(single),
        "tabular_batch_throughput_lots_per_s": _r(len(ctx["X_test"]) / batch_s, 1),
        "process_rss_mb": _r(_rss_mb(), 1),
    }
    ensemble_path = ctx["model_dir"] / "whatif_ensemble_v1.0.0.joblib"
    if ensemble_path.exists():
        from app.whatif.ensemble import load_ensemble, member_predictions

        bundle = load_ensemble(ctx["model_dir"])
        timings = []
        for snap in snapshots[:50]:
            start = time.perf_counter()
            member_predictions(bundle, single_row_to_model_matrix(snap, bundle.feature_columns))
            timings.append((time.perf_counter() - start) * 1000)
        out["whatif_ensemble_predict"] = _percentiles(timings)
    if vision_available:
        import torch

        from app.ml.wafer_model import load_latest_wafer_model, predict_batch

        model = load_latest_wafer_model("wafer_pattern_cnn", ctx["model_dir"])["model"]
        # The CNN's input is a one-hot {blank, pass, fail} tensor: (N, 3, 64, 64).
        x = np.random.default_rng(SEED).random((64, 3, 64, 64)).astype(np.float32)
        predict_batch(model, x[:1])
        timings = []
        for i in range(50):
            start = time.perf_counter()
            predict_batch(model, x[i % 64 : i % 64 + 1])
            timings.append((time.perf_counter() - start) * 1000)
        start = time.perf_counter()
        predict_batch(model, x)
        out["cnn_single_map"] = _percentiles(timings)
        out["cnn_batch_throughput_maps_per_s"] = _r(64 / (time.perf_counter() - start), 1)
        out["torch_threads"] = torch.get_num_threads()
    out["process_rss_mb_after"] = _r(_rss_mb(), 1)
    return out


# =============================================================================== report
def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def render_markdown(result: dict) -> str:
    L: list[str] = []
    s = result["split"]
    L += [
        "# WafGuard AI — Evaluation Benchmark",
        "",
        "> **SYNTHETIC DATA.** Every number is measured on the simulated fab (generator seed "
        f"{result['dataset']['seed']}, config `{result['dataset']['config_hash']}`) and says nothing about real-fab accuracy.",
        "> Baselines sit next to every result. There is deliberately no single \"accuracy\" number.",
        "",
        f"Generated {result['generated_at']} by `python -m app.evaluation.run_all` (wall time {result['wall_time_s']} s).",
        "",
        "## Test split",
        "",
        f"- {s['method']}",
        f"- train {s['n_train']} · val {s['n_val']} · **test {s['n_test']}** lots; test covers {s['test_start'][:10]} → {s['test_end'][:10]}"
        f" (train ends {s['train_end'][:10]}); low-yield rate in test {_fmt(s['test_low_yield_rate'])}",
        "",
        "## 1. Yield prediction (regression)",
        "",
        "| Model | MAE (pp) | RMSE (pp) | R² |",
        "|---|---|---|---|",
    ]
    for name, m in result["yield"]["models"].items():
        L.append(f"| {'**' + name + '**' if 'production' in name else name} | {_fmt(m['mae'])} | {_fmt(m['rmse'])} | {_fmt(m['r2'])} |")
    risk = result["risk"]
    L += [
        "",
        f"## 2. Low-yield risk (classification) — {risk['n_low_yield']} low-yield lots of {risk['n_test']}",
        "",
        "| Model | Precision | Recall | PR-AUC | ROC-AUC | Calibration ECE | False-alarm rate | Missed low-yield | Threshold |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name, m in risk["models"].items():
        L.append(
            f"| {'**' + name + '**' if 'production' in name else name} | {_fmt(m.get('precision'))} | {_fmt(m.get('recall'))} | {_fmt(m.get('pr_auc'))} | "
            f"{_fmt(m.get('roc_auc'))} | {_fmt(m.get('calibration_ece'))} | {_fmt(m.get('false_alarm_rate'))} | {_fmt(m.get('missed_low_yield'))} | {_fmt(m.get('threshold'))} |"
        )
    L += ["", *[f"- `{k}`: {v}" for k, v in risk["definitions"].items()], f"- production threshold: {risk['production_threshold_source']}", ""]

    rc = result.get("root_cause")
    L += ["## 3. Root cause ranking vs hidden ground truth", ""]
    if not rc:
        L += ["_Skipped (`--skip-rootcause`)._", ""]
    else:
        L += [
            f"Scored {rc['n_scored']} of {rc['n_faulty_test_lots']} faulty test lots; excluded as unmappable to any candidate: {rc['unmappable_excluded'] or 'none'}.",
            "",
            "| Ranking | Top-1 | Top-3 | Top-5 | MRR |",
            "|---|---|---|---|---|",
        ]
        for name, agg in rc["overall"].items():
            label = name.replace("_", " ")
            L.append(f"| {'**' + label + '**' if name == 'multi_signal_engine' else label} | {_fmt(agg.get('top1'))} | {_fmt(agg.get('top3'))} | {_fmt(agg.get('top5'))} | {_fmt(agg.get('mrr'))} |")
        L += ["", "Per fault type (multi-signal engine vs SHAP+deviation):", "", "| Fault | n | Engine Top-1 | Engine MRR | SHAP+dev Top-1 | SHAP+dev MRR |", "|---|---|---|---|---|---|"]
        for fault, agg in rc["per_fault"].items():
            e, b = agg["multi_signal_engine"], agg["shap_plus_deviation"]
            L.append(f"| {fault} | {e['n']} | {_fmt(e['top1'])} | {_fmt(e['mrr'])} | {_fmt(b['top1'])} | {_fmt(b['mrr'])} |")
        L += ["", *[f"- `{k}`: {v}" for k, v in rc["baseline_definitions"].items()], ""]

    sim = result["similarity"]
    L += [f"## 4. Historical lot similarity (k = {sim['k']})", "", f"Query: {sim['query']}; pool: {sim['pool']}.", "",
          "| Space | Search | Fault retrieval precision@k | Outcome consistency@k | Low-yield neighbour gap | Queries |", "|---|---|---|---|---|---|"]
    for space, res in sim["spaces"].items():
        if not res.get("available"):
            L.append(f"| {space} | unavailable | — | — | — | — |")
            continue
        for name in ("model", "random"):
            m = res[name]
            label = "nearest neighbours" if name == "model" else "random same-step lots (baseline)"
            L.append(f"| {space} | {label} | {_fmt(m.get('fault_retrieval_precision_at_k'))} | {_fmt(m.get('outcome_consistency_at_k'))} | {_fmt(m.get('low_yield_neighbour_rate_gap'))} | {m.get('n_queries')} |")
    L += ["", *[f"- `{k}`: {v}" for k, v in sim["definitions"].items()], ""]

    vis = result["vision"]
    L += ["## 5. Wafer-map vision", ""]
    if not vis.get("available"):
        L += [f"_Unavailable: {vis.get('reason')}_", ""]
    else:
        L += [f"> ⚠ {vis['warning']}", "", f"Split: {vis['split']} (n = {vis['n_test']}).", "",
              "| Model | Macro F1 | " + " | ".join(f"Recall {c}" for c in vis["classes"]) + " |",
              "|---|---|" + "---|" * len(vis["classes"])]
        for name, m in vis["models"].items():
            L.append(f"| {'**' + name + '**' if 'production' in name else name} | {_fmt(m['macro_f1'])} | " + " | ".join(_fmt(m["per_class_recall"][c]) for c in vis["classes"]) + " |")
        prod = next(m for n, m in vis["models"].items() if "production" in n)
        L += ["", "Production CNN confusion matrix (rows = true, columns = predicted):", "", "| | " + " | ".join(vis["classes"]) + " |", "|---|" + "---|" * len(vis["classes"])]
        for cls, row in zip(vis["classes"], prod["confusion_matrix"]):
            L.append(f"| {cls} | " + " | ".join(str(v) for v in row) + " |")
        L += ["", f"- {vis['comparison_note']}", ""]

    sysm = result["system"]
    L += ["## 6. System", "", f"{sysm['hardware_note']}.", "", "| Operation | p50 (ms) | p95 (ms) | Throughput |", "|---|---|---|---|"]
    L.append(f"| tabular predict, one lot (regressor + calibrated classifier) | {_fmt(sysm['tabular_single_lot_predict']['p50_ms'])} | {_fmt(sysm['tabular_single_lot_predict']['p95_ms'])} | {sysm['tabular_batch_throughput_lots_per_s']} lots/s (batch) |")
    if sysm.get("whatif_ensemble_predict"):
        L.append(f"| what-if ensemble, one lot | {_fmt(sysm['whatif_ensemble_predict']['p50_ms'])} | {_fmt(sysm['whatif_ensemble_predict']['p95_ms'])} | — |")
    if sysm.get("cnn_single_map"):
        L.append(f"| wafer CNN, one map | {_fmt(sysm['cnn_single_map']['p50_ms'])} | {_fmt(sysm['cnn_single_map']['p95_ms'])} | {sysm['cnn_batch_throughput_maps_per_s']} maps/s (batch of 64) |")
    if rc and rc.get("latency"):
        L.append(f"| root-cause engine, one lot (in-memory DB) | {_fmt(rc['latency']['p50_ms'])} | {_fmt(rc['latency']['p95_ms'])} | — |")
    L += ["", f"- Process memory (RSS): {sysm['process_rss_mb']} MB after loading models, {sysm['process_rss_mb_after']} MB at the end of the system pass.", ""]
    return "\n".join(L) + "\n"


def run(skip_rootcause: bool = False, max_rootcause_lots: int = 0) -> dict:
    started = time.perf_counter()
    settings = get_settings()
    ctx = load_context(settings)
    meta_path = ctx["data_dir"] / "_generation_metadata.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset": {"provenance": "SYNTHETIC", "seed": meta.get("seed"), "config_hash": dataset_version(settings), "n_lots": meta.get("n_lots")},
        "models": {"lot_yield_regressor": ctx["reg"]["version"], "lot_yield_classifier": ctx["clf"]["version"]},
        "split": split_info(ctx),
    }
    print("[benchmark] yield...", flush=True)
    result["yield"] = eval_yield(ctx)
    print("[benchmark] risk...", flush=True)
    result["risk"] = eval_risk(ctx)
    print("[benchmark] similarity...", flush=True)
    result["similarity"] = eval_similarity(ctx)
    print("[benchmark] vision...", flush=True)
    result["vision"] = eval_vision(ctx)
    print("[benchmark] system...", flush=True)
    result["system"] = eval_system(ctx, result["vision"].get("available", False))
    if not skip_rootcause:
        print("[benchmark] root cause (seeds an in-memory DB; several minutes)...", flush=True)
        result["root_cause"] = eval_rootcause(ctx, max_lots=max_rootcause_lots)
    else:
        result["root_cause"] = None
    result["wall_time_s"] = _r(time.perf_counter() - started, 1)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="WafGuard evaluation benchmark")
    parser.add_argument("--skip-rootcause", action="store_true", help="skip the slow root-cause section")
    parser.add_argument("--max-rootcause-lots", type=int, default=0, help="cap faulty test lots scored (0 = all)")
    parser.add_argument("--out-dir", default=str(REPORTS_DIR))
    args = parser.parse_args()

    result = run(skip_rootcause=args.skip_rootcause, max_rootcause_lots=args.max_rootcause_lots)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "benchmark.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    (out / "benchmark.md").write_text(render_markdown(result), encoding="utf-8")
    log_run(
        "benchmark",
        "benchmark",
        f"{result['models']['lot_yield_regressor']}/{result['models']['lot_yield_classifier']}",
        metrics={"yield": result["yield"]["models"].get("xgboost (production)", {}),
                 "risk": {k: v for k, v in result["risk"]["models"]["xgboost calibrated (production)"].items() if not isinstance(v, dict)}},
        artifacts=[out / "benchmark.json", out / "benchmark.md"],
    )
    print(f"\nWrote {out / 'benchmark.md'} and {out / 'benchmark.json'}")


if __name__ == "__main__":
    main()
