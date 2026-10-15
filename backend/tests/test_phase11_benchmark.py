"""
Phase 11 (brief P10) — evaluation benchmark.

The full benchmark runs on the 5,000-lot artifacts (python -m app.evaluation.run_all).
Here it runs end-to-end on the session's small test fab (root cause skipped:
it is the same harness as scripts/evaluate_rootcause.py), and its metric
helpers are pinned on hand-checkable inputs.
"""
import numpy as np
import pandas as pd
import pytest

from app.evaluation import run_all


@pytest.fixture(scope="module")
def benchmark(settings, trained_bundles, sim_tables, tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("bench_data")
    sim_tables["ground_truth"].to_parquet(data_dir / "ground_truth.parquet", index=False)
    original = settings.synthetic_data_path
    settings.synthetic_data_path = str(data_dir)
    try:
        yield run_all.run(skip_rootcause=True)
    finally:
        settings.synthetic_data_path = original


def test_every_section_reports_baselines_next_to_the_model(benchmark):
    yield_models = benchmark["yield"]["models"]
    assert "xgboost (production)" in yield_models
    assert any("mean predictor" in name for name in yield_models)
    assert any("ridge" in name for name in yield_models)

    risk_models = benchmark["risk"]["models"]
    assert "xgboost calibrated (production)" in risk_models
    assert {"base rate (baseline)", "logistic regression (baseline)", "spec-band rule (engineering baseline)"} <= set(risk_models)
    for name, metrics in risk_models.items():
        if "from its artifact" in name:
            continue
        for key in ("precision", "recall", "pr_auc", "false_alarm_rate", "missed_low_yield"):
            assert key in metrics, f"{name} lacks {key}"

    for space in benchmark["similarity"]["spaces"].values():
        if space.get("available"):
            assert "model" in space and "random" in space

    vision = benchmark["vision"]
    if vision["available"]:
        assert "SYNTHETIC" in vision["warning"]
        assert any("baseline" in name for name in vision["models"])
        prod = next(m for n, m in vision["models"].items() if "production" in n)
        assert len(prod["confusion_matrix"]) == len(vision["classes"])
        assert set(prod["per_class_recall"]) == set(vision["classes"])


def test_numbers_come_from_the_future_test_split(benchmark, trained_bundles):
    from app.ml.preprocessing import chronological_split

    _, _, test = chronological_split(trained_bundles["features_df"].copy())
    assert benchmark["split"]["n_test"] == len(test) == benchmark["yield"]["n_test"] == benchmark["risk"]["n_test"]
    assert benchmark["split"]["test_start"] > benchmark["split"]["train_end"]


def test_no_single_accuracy_number(benchmark):
    def keys(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield k
                yield from keys(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from keys(v)

    assert "accuracy" not in set(keys(benchmark))


def test_markdown_renders_all_sections(benchmark):
    md = run_all.render_markdown(benchmark)
    for heading in ("1. Yield prediction", "2. Low-yield risk", "3. Root cause", "4. Historical lot similarity", "5. Wafer-map vision", "6. System"):
        assert heading in md
    assert "SYNTHETIC DATA" in md and "Skipped" in md


def test_system_section_measures_latency(benchmark):
    system = benchmark["system"]
    assert system["tabular_single_lot_predict"]["p50_ms"] > 0
    assert system["tabular_single_lot_predict"]["p95_ms"] >= system["tabular_single_lot_predict"]["p50_ms"]
    assert system["tabular_batch_throughput_lots_per_s"] > 0


def test_classification_metrics_on_a_hand_checked_case():
    y = np.array([1, 1, 0, 0, 0, 0])
    score = np.array([0.9, 0.2, 0.8, 0.1, 0.1, 0.1])
    m = run_all._classification_metrics(y, score, threshold=0.5)
    assert m["confusion"] == {"tp": 1, "fp": 1, "fn": 1, "tn": 3}
    assert m["precision"] == 0.5 and m["recall"] == 0.5
    assert m["false_alarm_rate"] == 0.25 and m["false_discovery_rate"] == 0.5 and m["missed_low_yield"] == 1


def test_spec_band_rule_flags_out_of_band_runs():
    frame = pd.DataFrame({"rf_power_max": [860.0, 900.0], "rf_power_mean": [858.0, 858.0], "pressure_max": [2.8, 2.8], "pressure_mean": [2.7, 2.7]})
    assert run_all.spec_band_rule(frame).tolist() == [0.0, 1.0]
