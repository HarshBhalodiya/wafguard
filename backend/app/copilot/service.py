"""
Copilot entry point (Phase 8, brief P7).

    answer(db, question, lot_id=None)
      1. identify the lot (explicit id, or parsed from the question: LOT_10123 / L10123)
      2. retrieve evidence for the question's intent (retrieval FIRST)
      3. compose a deterministic, fully cited answer (the no-LLM fallback)
      4. if an LLM provider is configured, let it rephrase — and keep its output
         only if `validate_rephrasing` accepts it (citations exist, no new numbers)

The response always carries the sentences with their citations, the evidence
items themselves, which engine wrote the words, and `insufficient_evidence`.
"""
from __future__ import annotations

from app.config import get_settings
from app.copilot import compose, evidence, providers
from app.utils.logging import get_logger

logger = get_logger(__name__)

# Which retrievers run for which intent (all need the lot except docs).
RETRIEVERS = {
    "risk": ("prediction", "attributions", "root_cause", "timeline", "equipment", "defects", "similar", "recommendations"),
    "root_cause": ("prediction", "root_cause", "timeline", "equipment", "defects"),
    "recommendation": ("root_cause", "recommendations"),
    "similar": ("prediction", "similar"),
    "equipment": ("equipment", "timeline"),
    "defects": ("defects", "root_cause"),
    "summary": ("prediction", "root_cause", "recommendations"),
}
_FUNCS = {
    "prediction": evidence.retrieve_prediction,
    "attributions": evidence.retrieve_attributions,
    "root_cause": evidence.retrieve_root_cause,
    "timeline": evidence.retrieve_timeline,
    "equipment": evidence.retrieve_equipment,
    "defects": evidence.retrieve_defects,
    "similar": evidence.retrieve_similar,
    "recommendations": evidence.retrieve_recommendations,
}


def answer(db, question: str, lot_id: str | None = None, provider: providers.LLMProvider | None = None) -> dict:
    question = (question or "").strip()
    lot_id = lot_id or compose.extract_lot_id(question)
    intent = compose.detect_intent(question)
    ev = evidence.EvidenceSet()

    lot = evidence.find_lot(db, lot_id)
    if lot_id and lot is None:
        ev.missing["lot"] = f"lot {lot_id} was not found"
        sentences = [{"text": f"{compose.NOT_ENOUGH} to answer: lot {lot_id} was not found.", "citations": []}]
        return _response(question, lot_id, intent, sentences, ev, True, None, "deterministic_template")

    if lot is not None:
        for name in RETRIEVERS[intent]:
            try:
                _FUNCS[name](db, lot, ev)
            except Exception as exc:  # noqa: BLE001 - one broken retriever must not sink the answer
                logger.exception("copilot retriever %s failed", name)
                ev.missing[name] = f"retriever failed: {exc}"
    evidence.retrieve_docs(question, ev, k=1 if lot is not None else 2)

    sentences, insufficient = compose.compose(intent, ev, lot)

    provider = provider if provider is not None else providers.provider_from_settings(get_settings())
    source, provider_name, rejection = "deterministic_template", None, None
    if provider is not None and not insufficient:
        try:
            rephrased = provider.rephrase(question, sentences, ev.to_list())
            ok, rejection = providers.validate_rephrasing(rephrased, sentences, set(ev.items))
            if ok:
                sentences, source, provider_name = rephrased, f"llm:{provider.name}", provider.name
        except Exception as exc:  # noqa: BLE001 - an LLM outage degrades to the cited template
            rejection = f"provider error: {exc}"
            logger.warning("copilot provider %s failed: %s", getattr(provider, "name", "?"), exc)
    response = _response(question, lot_id, intent, sentences, ev, insufficient, provider_name, source)
    response["llm_rejected_reason"] = rejection
    return response


def _response(question, lot_id, intent, sentences, ev, insufficient, provider_name, source) -> dict:
    return {
        "question": question,
        "lot_id": lot_id,
        "intent": intent,
        "answer": compose.render(sentences),
        "sentences": sentences,
        "evidence": ev.to_list(),
        "missing_evidence": ev.missing,
        "insufficient_evidence": insufficient,
        "source": source,
        "llm_provider": provider_name,
        "label": "Answer composed only from the cited evidence items. Root causes are candidates, not confirmed causes. Data is SYNTHETIC.",
    }
