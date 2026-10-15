"""
Phase 5 tests (P5-4): measured case statistics (backward-looking only, no
recovery claims), the templates knowledge-base file, and the recommendation
wiring that attaches case statistics to every item.
"""
import json

from app.services import case_statistics, recommendation_service
from app.services.rootcause_service import CANDIDATE_DEFINITIONS


def test_case_stats_are_backward_looking(db, incident_lot_id):
    lot = db.lots.find_one({"lot_id": incident_lot_id})
    stats = case_statistics.compute_case_stats(db, incident_lot_id)
    assert stats["n_similar"] > 0
    assert stats["n_similar"] + stats["n_similar"] == 2 * len(stats["neighbors"])
    for neighbor in stats["neighbors"]:
        prior = db.lots.find_one({"lot_id": neighbor["lot_id"]})
        assert prior["process_start"] < lot["process_start"], "case stats may never count a lot processed after this one"
        assert prior["process_step"] == lot["process_step"]
    assert stats["low_yield"] + stats["healthy"] == stats["n_similar"]


def test_case_stats_first_lot_has_no_history(db):
    first = db.lots.find_one({}, sort=[("process_start", 1)])
    stats = case_statistics.compute_case_stats(db, first["lot_id"])
    assert stats["n_similar"] == 0
    assert "Not enough comparable history" in stats["note"]


def test_case_stats_never_claim_recovery(db, incident_lot_id):
    stats = case_statistics.compute_case_stats(db, incident_lot_id)
    blob = json.dumps(stats).lower()
    assert "recover" not in blob, "no intervention events exist in this dataset — a recovery claim would be invented"
    assert "measured outcomes" in stats["source"]


def test_templates_live_in_documented_data_file():
    templates = recommendation_service.load_templates()
    for candidate in CANDIDATE_DEFINITIONS:
        assert candidate in templates, f"knowledge base must cover candidate {candidate}"
    raw = json.loads(recommendation_service.TEMPLATES_FILE.read_text(encoding="utf-8"))
    assert raw["provenance"]
    assert raw["version"]


def test_recommendations_attach_case_statistics(db, incident_lot_id):
    result = recommendation_service.generate_recommendations(db, incident_lot_id, force_recompute=True)
    assert result["case_statistics"]["n_similar"] > 0
    assert result["recommendation_items"], "expected grounded recommendation items"
    for item in result["recommendation_items"]:
        stats = item["case_statistics"]
        assert stats["n_similar"] == result["case_statistics"]["n_similar"]
        assert stats["neighbors"]
        assert "recovered" not in json.dumps(stats).lower()
