"""
CopilotService (spec section 44) — an explanation/orchestration layer, not
a source of manufacturing truth. It never invents a fact: every field in
the returned `evidence` bundle comes from a verified service call (lot
data, the trained models, the root-cause engine, similarity search,
recommendations). An LLM — if one is configured — only *phrases* that
retrieved bundle in natural language; it is never asked to produce a
number or a claim on its own.

Without an `LLM_API_KEY`, `answer_question` returns a deterministic
templated narrative, so the app never requires external credentials to
demonstrate this feature end-to-end.

Phase 4 (P4-6/P4-7, provider honesty): the phrasing layer names the
provider that actually answered. Supported providers are exactly:
`anthropic` and `openai_compatible` (any OpenAI-style `/chat/completions`
endpoint, self-hosted or vendor — configured via `LLM_BASE_URL`). No other
vendor is claimed anywhere in code or docs, and when nothing is configured
the response says `llm_provider: null` with a deterministic template.
"""
from typing import Optional

import requests

from app.config import get_settings
from app.database.repositories.lot_repository import LotRepository
from app.database.repositories.rootcause_repository import RootCauseRepository
from app.services import recommendation_service, rootcause_service, similarity_service
from app.utils.exceptions import NotFoundError
from app.utils.logging import get_logger

logger = get_logger(__name__)

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"


def _gather_evidence(db, lot_id: str) -> dict:
    """The only source of facts this service is allowed to use."""
    lot = LotRepository(db).get_by_lot_id(lot_id)
    if not lot:
        raise NotFoundError(f"Lot {lot_id} was not found.")

    rootcause_repo = RootCauseRepository(db)
    root_cause = rootcause_repo.latest_for_lot(lot_id) or rootcause_service.compute_root_cause(db, lot_id)
    recommendation = recommendation_service.generate_recommendations(db, lot_id)
    try:
        similar = similarity_service.find_similar_lots(db, lot_id, k=3)
    except NotFoundError:
        similar = None

    top_candidate = root_cause["candidates"][0] if root_cause["candidates"] else None

    return {
        "lot_id": lot_id,
        "equipment": lot["equipment"],
        "process_step": lot["process_step"],
        "predicted_yield": lot.get("predicted_yield"),
        "risk_probability": lot.get("risk_probability"),
        "risk_level": lot.get("risk_level"),
        "top_root_cause_candidate": top_candidate["candidate"] if top_candidate else None,
        "top_root_cause_confidence": top_candidate["confidence"] if top_candidate else None,
        "top_root_cause_score": top_candidate["score"] if top_candidate else None,
        "top_recommendation": recommendation["recommendations"][0] if recommendation["recommendations"] else None,
        "similar_lot_insight": similar["insight"] if similar else None,
    }


def _deterministic_narrative(evidence: dict) -> str:
    """Always available — no external call, no external credentials required."""
    if evidence["risk_level"] is None:
        return f"Lot {evidence['lot_id']} has not been scored yet — no prediction is on file for it."

    parts = [
        f"Lot {evidence['lot_id']} (on {evidence['equipment']}, {evidence['process_step']}) is at "
        f"{round((evidence['risk_probability'] or 0) * 100)}% predicted low-yield risk ({evidence['risk_level']}), "
        f"with a predicted yield of {evidence['predicted_yield']}%."
    ]
    if evidence["top_root_cause_candidate"]:
        parts.append(
            f"The strongest root-cause candidate is {evidence['top_root_cause_candidate']} "
            f"({evidence['top_root_cause_confidence']} confidence, score {evidence['top_root_cause_score']:.2f}) — "
            "a model-attributed candidate, not a confirmed cause."
        )
    if evidence["similar_lot_insight"]:
        parts.append(evidence["similar_lot_insight"])
    if evidence["top_recommendation"]:
        parts.append(f"Suggested next step: {evidence['top_recommendation']}")
    return " ".join(parts)


SYSTEM_PROMPT = (
    "You explain semiconductor lot risk to a process engineer using ONLY the JSON facts "
    "given to you. Never invent a number, sensor value, or cause. Call root-cause findings "
    "'candidates', never 'confirmed causes'. Keep it to 3-4 sentences."
)


def _call_anthropic(evidence: dict, question: str, settings) -> str:
    response = requests.post(
        ANTHROPIC_MESSAGES_URL,
        headers={
            "x-api-key": settings.llm_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": settings.llm_model or "claude-3-5-haiku-latest",
            "max_tokens": 300,
            "system": SYSTEM_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": f"Question: {question or 'Why is this lot at risk?'}\n\nVerified facts: {evidence}",
                }
            ],
        },
        timeout=10,
    )
    response.raise_for_status()
    content = response.json().get("content", [])
    return "".join(block.get("text", "") for block in content if block.get("type") == "text")


def _call_openai_compatible(evidence: dict, question: str, settings) -> str:
    base = (settings.llm_base_url or "https://api.openai.com/v1").rstrip("/")
    response = requests.post(
        f"{base}/chat/completions",
        headers={
            "authorization": f"Bearer {settings.llm_api_key}",
            "content-type": "application/json",
        },
        json={
            "model": settings.llm_model or "gpt-4o-mini",
            "max_tokens": 300,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Question: {question or 'Why is this lot at risk?'}\n\nVerified facts: {evidence}",
                },
            ],
        },
        timeout=10,
    )
    response.raise_for_status()
    choices = response.json().get("choices", [])
    return choices[0].get("message", {}).get("content", "") if choices else ""


PROVIDERS = {
    "anthropic": _call_anthropic,
    "openai_compatible": _call_openai_compatible,
}


def _llm_narrative(evidence: dict, question: str, settings) -> tuple[Optional[str], Optional[str]]:
    """Phrases the already-retrieved `evidence` naturally via the configured
    provider. Returns (text, provider); (None, None) when no provider is
    configured or on ANY failure — the caller falls back to the deterministic
    narrative, because a copilot outage must never break the underlying feature."""
    provider = (settings.llm_provider or "").strip().lower()
    if not settings.llm_api_key or provider not in PROVIDERS:
        return None, None
    try:
        text = PROVIDERS[provider](evidence, question, settings)
        return (text.strip() or None), provider
    except Exception:  # noqa: BLE001 - a copilot failure must degrade, never propagate
        logger.exception("LLM narrative call (%s) failed; falling back to the deterministic explainer.", provider)
        return None, None


def answer_question(db, lot_id: str, question: str = "") -> dict:
    settings = get_settings()
    evidence = _gather_evidence(db, lot_id)

    narrative, provider = _llm_narrative(evidence, question, settings)
    if narrative is None:
        narrative = _deterministic_narrative(evidence)
        source = "deterministic_template"
    else:
        source = f"llm:{provider}"

    return {
        "lot_id": lot_id,
        "question": question,
        "answer": narrative,
        "source": source,
        # Honest provider contract (P4-7): which engine phrased this answer.
        # Null when no LLM is configured — the answer is a deterministic template.
        "llm_provider": provider,
        "evidence": evidence,
    }
