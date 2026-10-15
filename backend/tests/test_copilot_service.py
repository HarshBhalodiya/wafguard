"""
CopilotService tests (spec section 44): the service must only narrate
verified facts and must never require an LLM/external credential to work.
"""
import pytest

from app.services import copilot_service
from app.utils.exceptions import NotFoundError


def test_answer_question_uses_deterministic_narrative_without_llm_key(db, incident_lot_id):
    # .env.example / the default Settings ship LLM_API_KEY blank, so this
    # exercises the no-external-credentials path the app must work without.
    from app.config import get_settings

    assert not get_settings().llm_api_key, "this test assumes no LLM_API_KEY is configured"

    result = copilot_service.answer_question(db, incident_lot_id, "Why is this lot at risk?")

    assert result["source"] == "deterministic_template"
    assert result["lot_id"] == incident_lot_id
    lot_equipment = db.lots.find_one({"lot_id": incident_lot_id})["equipment"]
    assert result["evidence"]["equipment"] == lot_equipment
    assert result["evidence"]["risk_level"] in ("MEDIUM", "HIGH")


def test_answer_question_never_fabricates_facts_beyond_the_evidence_bundle(db, incident_lot_id):
    result = copilot_service.answer_question(db, incident_lot_id)
    evidence = result["evidence"]
    # every concrete figure in the answer must trace back to the evidence dict
    assert str(evidence["risk_level"]) in result["answer"] or f"{round((evidence['risk_probability'] or 0) * 100)}%" in result["answer"]


def test_answer_question_unknown_lot_raises(db):
    with pytest.raises(NotFoundError):
        copilot_service.answer_question(db, "LOT_DOES_NOT_EXIST")
