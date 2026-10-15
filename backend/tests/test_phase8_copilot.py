"""
Phase 8 (brief P7) — retrieval-first copilot with cited evidence.

Fixed questions are asked against the seeded test fab. For every answer:
each sentence carries at least one citation, and every cited id exists in the
returned evidence (and therefore came from a real retrieval). Missing evidence
must produce "Not enough evidence", and an LLM that invents a citation or a
number must be rejected in favour of the deterministic draft.
"""
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.copilot import compose, providers, service
from app.main import app

FIXED_QUESTIONS = [
    ("Why is {lot} high risk?", "risk"),
    ("What is the root cause of {lot}?", "root_cause"),
    ("What should I do next for {lot}?", "recommendation"),
    ("Show lots similar to {lot}", "similar"),
    ("What is the equipment and maintenance state for {lot}?", "equipment"),
    ("Which defect pattern did inspection find on {lot}?", "defects"),
]


def _assert_fully_cited(result: dict) -> None:
    evidence_ids = {item["id"] for item in result["evidence"]}
    assert result["sentences"], "an answer must have at least one sentence"
    for sentence in result["sentences"]:
        assert sentence["citations"], f"uncited sentence: {sentence['text']}"
        for citation in sentence["citations"]:
            assert citation in evidence_ids, f"citation {citation} is not a retrieved evidence item"


@pytest.mark.parametrize("template, intent", FIXED_QUESTIONS)
def test_fixed_questions_are_fully_cited(db, incident_lot_id, template, intent):
    result = service.answer(db, template.format(lot=incident_lot_id))
    assert result["intent"] == intent
    assert result["lot_id"] == incident_lot_id
    assert result["insufficient_evidence"] is False, result["missing_evidence"]
    _assert_fully_cited(result)
    assert result["source"] == "deterministic_template" and result["llm_provider"] is None


def test_cited_values_match_the_stored_records(db, incident_lot_id):
    result = service.answer(db, f"Why is {incident_lot_id} high risk?")
    items = {i["id"]: i for i in result["evidence"]}
    prediction = db.predictions.find_one({"lot_id": incident_lot_id}, sort=[("timestamp", -1)])
    pred_item = items[f"pred:{incident_lot_id}"]
    assert pred_item["risk_level"] == prediction["risk_level"]
    assert pred_item["predicted_yield"] == prediction["predicted_yield"]
    for item in result["evidence"]:
        if item["type"] == "similar_lot":
            assert db.lots.find_one({"lot_id": item["lot_id"]})["yield"] == item["lot_yield"]
        if item["type"] == "recommendation":
            assert db.interventions.count_documents({"action_id": item["id"].split(":")[-1]}) >= item["n_cases"]
    assert "candidate" in result["answer"] and "not a confirmed cause" in result["answer"]


def test_short_lot_id_form_is_understood(db, incident_lot_id):
    short = "L" + incident_lot_id.split("_")[1]
    assert service.answer(db, f"Why is {short} high risk?")["lot_id"] == incident_lot_id


def test_unknown_lot_says_not_enough_evidence(db):
    result = service.answer(db, "Why is LOT_99999999 high risk?")
    assert result["insufficient_evidence"] is True
    assert result["answer"].startswith("Not enough evidence")


def test_missing_prediction_says_not_enough_evidence(db, incident_lot_id):
    lot = db.lots.find_one({"lot_id": incident_lot_id})
    clone = {k: v for k, v in lot.items() if k != "_id"}
    clone["lot_id"] = "LOT_9900001"
    db.lots.insert_one(clone)
    try:
        result = service.answer(db, "Why is LOT_9900001 high risk?")
        assert result["insufficient_evidence"] is True
        assert "Not enough evidence" in result["answer"] and "no prediction" in result["answer"]
    finally:
        db.lots.delete_one({"lot_id": "LOT_9900001"})


def test_question_without_a_lot_uses_docs_or_refuses(db):
    docs = service.answer(db, "How is the risk score methodology weighted?")
    if docs["insufficient_evidence"]:
        assert docs["answer"].startswith("Not enough evidence")
    else:
        _assert_fully_cited(docs)
        assert all(c.startswith("doc:") for s in docs["sentences"] for c in s["citations"])
    nonsense = service.answer(db, "zzzz qqqq")
    assert nonsense["insufficient_evidence"] is True


# --------------------------------------------------------------------------- providers
class _FakeProvider:
    name = "fake"

    def __init__(self, transform):
        self.transform = transform

    def rephrase(self, question, sentences, evidence):
        return self.transform(sentences)


def test_valid_llm_rephrasing_is_used(db, incident_lot_id):
    provider = _FakeProvider(lambda draft: [{"text": "In short: " + s["text"], "citations": s["citations"]} for s in draft])
    result = service.answer(db, f"Why is {incident_lot_id} high risk?", provider=provider)
    assert result["source"] == "llm:fake" and result["llm_provider"] == "fake"
    assert result["sentences"][0]["text"].startswith("In short:")
    _assert_fully_cited(result)


@pytest.mark.parametrize(
    "transform, reason",
    [
        (lambda d: [{"text": s["text"], "citations": ["pred:FAKE"]} for s in d], "unknown evidence"),
        (lambda d: [{"text": "Yield will recover to 99.7% after a fix.", "citations": d[0]["citations"]}], "introduces numbers"),
        (lambda d: [{"text": d[0]["text"], "citations": []}], "without citing"),
        (lambda d: [], "empty"),
    ],
)
def test_invalid_llm_output_falls_back_to_the_cited_draft(db, incident_lot_id, transform, reason):
    result = service.answer(db, f"Why is {incident_lot_id} high risk?", provider=_FakeProvider(transform))
    assert result["source"] == "deterministic_template"
    assert reason in (result["llm_rejected_reason"] or "")
    _assert_fully_cited(result)


def test_provider_errors_degrade_gracefully(db, incident_lot_id):
    def boom(_draft):
        raise RuntimeError("provider down")

    result = service.answer(db, f"Why is {incident_lot_id} high risk?", provider=_FakeProvider(boom))
    assert result["source"] == "deterministic_template" and "provider down" in result["llm_rejected_reason"]


def test_providers_are_swappable_behind_one_interface(settings, monkeypatch):
    providers.register_provider("unit_test", lambda s: _FakeProvider(lambda d: d))
    monkeypatch.setattr(settings, "llm_provider", "unit_test")
    assert providers.provider_from_settings(settings).name == "fake"
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(settings, "llm_api_key", "")
    assert providers.provider_from_settings(settings) is None, "no key -> no provider -> deterministic fallback"


def test_lot_id_extraction():
    assert compose.extract_lot_id("Why is Lot L19382 high risk?") == "LOT_19382"
    assert compose.extract_lot_id("lot-10001 status") == "LOT_10001"
    assert compose.extract_lot_id("no lot here") is None


# --------------------------------------------------------------------------- API
def test_copilot_ask_endpoint(db, incident_lot_id):
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            assert client.post("/api/copilot/ask", json={"question": "why?"}).status_code == 401
            token = client.post("/api/auth/login", json={"username": "engineer", "password": "wafguard-engineer"}).json()
            client.headers["Authorization"] = f"Bearer {token['data']['access_token']}"
            response = client.post("/api/copilot/ask", json={"question": "Why is this lot high risk?", "lot_id": incident_lot_id})
            assert response.status_code == 200, response.text
            _assert_fully_cited(response.json()["data"])
            assert client.post("/api/copilot/ask", json={"question": ""}).status_code == 422
    finally:
        app.dependency_overrides.clear()
