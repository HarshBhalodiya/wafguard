"""
Phase 3 tests (P3-8): evidence tagging + ids, the measured (not hardcoded)
pattern table, the extended-feature & temporal-shape evidence, the
multi-space similarity path, and recommendation grounding (no orphan
suggestions). Runs against the seeded+trained mongomock DB from conftest,
with the measured pattern-cause table injected from the in-process dataset.
"""
from pathlib import Path

import pandas as pd
import pytest

from app.config import PROCESS_PARAMETER_RANGES
from app.evaluation.pattern_associations import association_for, build_pattern_cause_table
from app.services import recommendation_service, rootcause_service, similarity_service
from app.utils.exceptions import NotFoundError

ALLOWED_KINDS = {"FACT", "MODEL_OUTPUT", "INFERENCE", "RECOMMENDATION"}


# --------------------------------------------------------------------------- evidence tagging
def test_every_evidence_item_is_tagged_with_id_kind_and_source(db, incident_lot_id):
    result = rootcause_service.compute_root_cause(db, incident_lot_id, top_n=10)
    assert result["candidates"]
    ids = []
    for candidate in result["candidates"]:
        assert candidate["evidence"], f"candidate {candidate['candidate']} must carry evidence"
        for item in candidate["evidence"]:
            assert item["kind"] in ALLOWED_KINDS, f"bad kind {item['kind']!r}"
            assert item["source"], "every evidence item must cite a source"
            assert item["evidence_id"].startswith(f"rc:{incident_lot_id}:")
            ids.append(item["evidence_id"])
    assert len(ids) == len(set(ids)), "evidence ids must be unique within one lot's result"


def test_hardcoded_pattern_boost_dict_is_gone():
    assert not hasattr(rootcause_service, "WAFER_PATTERN_CANDIDATE_BOOST")
    source = Path(rootcause_service.__file__).read_text(encoding="utf-8")
    assert "WAFER_PATTERN_CANDIDATE_BOOST" not in source


def test_equipment_and_temporal_signals_have_matching_evidence(db, incident_lot_id):
    """Internal consistency: any signal > 0.05 must appear as an evidence item."""
    result = rootcause_service.compute_root_cause(db, incident_lot_id, top_n=10)
    for candidate in result["candidates"]:
        breakdown = candidate["signal_breakdown"]
        kinds = {e["signal"] for e in candidate["evidence"]}
        if breakdown["equipment_association"] > 0.05:
            assert "equipment_association" in kinds
        if breakdown["temporal_evidence"] > 0.05:
            assert "temporal_evidence" in kinds
        # P3-4: temporal evidence phrased from the per-channel shape (slope present)
        temporal_items = [e for e in candidate["evidence"] if e["signal"] == "temporal_evidence"]
        for item in temporal_items:
            assert "OLS slope" in item["label"]
            assert "extended features" in item["source"] or "slope" in item["source"]


def test_temporal_shape_distinguishes_ramp_from_step():
    lo, hi = PROCESS_PARAMETER_RANGES["rf_power"]
    half_width = (hi - lo) / 2

    ramp_snapshot = {
        "rf_power_slope": 0.6 * half_width / 48,
        "rf_power_drift": 0.6 * half_width * 0.9,
        "rf_power_time_outside_pct": 0.5,
    }
    step_snapshot = {
        "rf_power_slope": 0.01 * 0.6 * half_width / 48,
        "rf_power_drift": 0.6 * half_width,
        "rf_power_time_outside_pct": 0.1,
    }
    ramp_score, ramp_ev = rootcause_service._temporal_shape("rf_power", "RF Power", ramp_snapshot)
    step_score, step_ev = rootcause_service._temporal_shape("rf_power", "RF Power", step_snapshot)

    assert 0.0 <= ramp_score <= 1.0 and 0.0 <= step_score <= 1.0
    assert "ramp" in ramp_ev["label"]
    assert "step change" in step_ev["label"]
    assert ramp_ev["kind"] == "FACT" and step_ev["kind"] == "FACT"


# --------------------------------------------------------------------------- measured pattern table
def test_pattern_signal_equals_measured_table_value(db, incident_lot_id, injected_pattern_cause_table):
    result = rootcause_service.compute_root_cause(db, incident_lot_id, top_n=10)
    pattern = result["dominant_wafer_pattern"]
    if pattern == "None":
        pytest.skip("incident lot has no dominant wafer pattern")
    for candidate in result["candidates"]:
        entry = association_for(injected_pattern_cause_table, pattern, candidate["candidate"])
        expected = entry["smoothed_rate"] if entry else 0.0
        assert candidate["signal_breakdown"]["wafer_pattern"] == pytest.approx(expected, abs=1e-4), (
            f"{candidate['candidate']} pattern signal must equal the measured table value"
        )


def test_pattern_table_builder_measures_counts_with_source_and_split():
    lots = pd.DataFrame(
        {
            "lot_id": [f"L{i}" for i in range(4)],
            "process_start": pd.date_range("2026-01-01", periods=4, tz="UTC").astype(str),
        }
    )
    gt = pd.DataFrame(
        {
            "lot_id": [f"L{i}" for i in range(4)],
            "true_fault": [
                "rf_power_drift",
                "none",
                "rf_power_drift",
                "chamber_pressure_instability",
            ],
            "spatial_signature": ["Edge-Ring"] * 4,
            "process_start": pd.date_range("2026-01-01", periods=4, tz="UTC").astype(str),
        }
    )
    table = build_pattern_cause_table(lots, gt, train_frac=1.0)

    rf = association_for(table, "Edge-Ring", "RF Power")
    pressure = association_for(table, "Edge-Ring", "Pressure")
    assert rf is not None and pressure is not None
    # 2 rf lots / 4 patterned lots, fractional credit doesn't apply (single-mapped faults)
    assert rf["hits"] == 2.0
    assert rf["pattern_lots"] == 4
    assert rf["rate"] == 0.5
    assert pressure["rate"] == 0.25
    # provenance must travel with the table
    assert "ground_truth" in table["source"]["dataset"]
    assert "train split" in table["source"]["split"]
    assert table["table_version"]
    # unmappable faults are declared, not hidden
    assert "sensor_dropout" in table["unmappable_faults_in_train"] or table["unmappable_faults_in_train"] == {}


def test_association_table_skips_patterns_without_measured_support():
    lots = pd.DataFrame({"lot_id": ["L1", "L2"], "process_start": ["2026-01-01", "2026-01-02"]})
    gt = pd.DataFrame(
        {
            "lot_id": ["L1", "L2"],
            "true_fault": ["none", "none"],
            "spatial_signature": ["Donut", "Donut"],
            "process_start": ["2026-01-01", "2026-01-02"],
        }
    )
    table = build_pattern_cause_table(lots, gt, train_frac=1.0)
    # no faulty lots at all -> no (pattern, candidate) associations may be invented
    assert table["associations"] == []
    assert association_for(table, "Donut", "RF Power") is None


# --------------------------------------------------------------------------- measured equipment stats
def test_low_sample_equipment_is_shrunk_toward_fab_baseline():
    # identical-to-baseline rates score 0 regardless of sample size
    score_eq, detail_eq = rootcause_service._relative_rate_score(50, 500, 50, 500)
    assert score_eq == pytest.approx(0.0, abs=1e-9)
    # zero prior lots: shrunk entirely to the fab baseline -> 0, flagged low-sample
    score_empty, detail_empty = rootcause_service._relative_rate_score(0, 0, 50, 500)
    assert score_empty == pytest.approx(0.0, abs=1e-9)
    assert detail_empty["low_sample"] is True
    # double the fab baseline with a healthy sample -> strong signal, not flagged
    score_double, detail_double = rootcause_service._relative_rate_score(100, 500, 50, 500)
    assert score_double > 0.5
    assert detail_double["low_sample"] is False
    # same raw rate (1 event / 5 lots == 100 / 500 == 20%, fab baseline 10%):
    # the tiny sample must be shrunk hard toward the baseline, the big sample not
    score_small, detail_small = rootcause_service._relative_rate_score(1, 5, 50, 500)
    score_big, detail_big = rootcause_service._relative_rate_score(100, 500, 50, 500)
    assert score_small < score_big
    assert detail_small["low_sample"] is True
    assert detail_small["n_lots"] == 5
    assert detail_big["low_sample"] is False


# --------------------------------------------------------------------------- recommendation grounding
def test_recommendations_are_grounded_in_evidence_ids(db, incident_lot_id):
    rc = rootcause_service.compute_root_cause(db, incident_lot_id)
    evidence_ids = {e["evidence_id"] for c in rc["candidates"] for e in c["evidence"]}
    assert evidence_ids

    result = recommendation_service.generate_recommendations(db, incident_lot_id, force_recompute=True)
    items = result["recommendation_items"]
    assert items, "expected at least one grounded recommendation item"
    for item in items:
        assert item["grounded_in"], "orphan suggestion: no evidence grounding"
        assert set(item["grounded_in"]) <= evidence_ids, "grounding must reference real evidence ids"
        assert item["candidate"] in {c["candidate"] for c in rc["candidates"]}
        assert item["text"]
    # legacy list kept in sync for existing UI/tests
    assert len(result["recommendations"]) == len(items)
    lot = db.lots.find_one({"lot_id": incident_lot_id})
    for step in result["recommendations"]:
        assert lot["equipment"] in step


def test_cached_legacy_rootcause_without_ids_triggers_recompute(db, incident_lot_id):
    """Pre-Phase-3 cached results (no evidence_id) are detected and recomputed."""
    assert recommendation_service._has_evidence_ids({"candidates": [{"evidence": []}]}) is True
    assert (
        recommendation_service._has_evidence_ids(
            {"candidates": [{"evidence": [{"label": "x", "kind": "FACT", "source": "s"}]}]}
        )
        is False
    )
    assert (
        recommendation_service._has_evidence_ids(
            {"candidates": [{"evidence": [{"label": "x", "evidence_id": "rc:1:a:0"}]}]}
        )
        is True
    )


# --------------------------------------------------------------------------- multi-space similarity
def test_similarity_multi_exposes_all_spaces_with_provenance(db, incident_lot_id):
    result = similarity_service.find_similar_multi(db, incident_lot_id, k=5)
    assert result["lot_id"] == incident_lot_id
    assert set(result["spaces"]) == {"parameter", "temporal", "vision"}

    parameter = result["spaces"]["parameter"]
    assert parameter["available"] is True
    assert parameter["matches"], "parameter space should return matches"
    for match in parameter["matches"]:
        assert match["space"] == "parameter"
        assert match["lot_id"] != incident_lot_id
        assert 0.0 <= match["similarity_score"] <= 1.0

    for space in ("temporal", "vision"):
        entry = result["spaces"][space]
        if entry["available"]:
            assert entry["matches"], f"{space} claims available but returned no matches"
            assert entry["coverage"]["coverage_note"]
            for match in entry["matches"]:
                assert match["space"] == space
                assert match["lot_id"] != incident_lot_id
                assert "cosine" in match
                assert 0.0 <= match["similarity_score"] <= 1.0
        else:
            # honest degradation: a reason, never fake matches
            assert entry["reason"]
            assert entry["matches"] == []


def test_similarity_multi_rejects_unknown_space(db, incident_lot_id):
    with pytest.raises(NotFoundError):
        similarity_service.find_similar_multi(db, incident_lot_id, spaces=("parameter", "nope"))


def test_similarity_multi_space_filter(db, incident_lot_id):
    result = similarity_service.find_similar_multi(db, incident_lot_id, k=3, spaces=("parameter",))
    assert set(result["spaces"]) == {"parameter"}
    assert len(result["spaces"]["parameter"]["matches"]) <= 3


def test_legacy_similarity_endpoint_shape_unchanged(db, incident_lot_id):
    result = similarity_service.find_similar_lots(db, incident_lot_id, k=5)
    assert "similar_lots" in result and "insight" in result
    for neighbor in result["similar_lots"]:
        assert 0.0 <= neighbor["similarity_score"] <= 1.0
        assert neighbor["space"] == "parameter"


# --------------------------------------------------------------------------- API path
@pytest.fixture()
def client(db):
    from app.api.deps import get_db
    from app.main import app
    from fastapi.testclient import TestClient

    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        login = test_client.post("/api/auth/login", json={"username": "admin", "password": "wafguard-admin"})
        assert login.status_code == 200, login.text
        test_client.headers["Authorization"] = f"Bearer {login.json()['data']['access_token']}"
        yield test_client
    app.dependency_overrides.clear()


def test_new_similarity_api_path_serves_multi_space_results(client, incident_lot_id):
    response = client.get(f"/api/similarity/{incident_lot_id}?k=3")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["lot_id"] == incident_lot_id
    assert set(data["spaces"]) == {"parameter", "temporal", "vision"}
    assert data["spaces"]["parameter"]["available"] is True


def test_old_similarity_api_path_still_works(client, incident_lot_id):
    response = client.get(f"/api/similar/{incident_lot_id}?k=3")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert "similar_lots" in data
