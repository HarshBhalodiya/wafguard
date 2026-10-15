"""
Deterministic, cited answer composer.

Turns an EvidenceSet into sentences, each `{"text", "citations"}`, where every
citation is an evidence id that exists in the set. This is both the no-LLM
answer and the draft an LLM provider may only rephrase. Intent routing is a
keyword table (documented below) — predictable, testable, no model involved.
"""
from __future__ import annotations

import re

from app.copilot.evidence import EvidenceSet

NOT_ENOUGH = "Not enough evidence"

# intent -> (keywords, evidence types that MUST be present to answer)
INTENTS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "recommendation": (("what should", "recommend", "action", "fix", "do next", "next step"), ("recommendation",)),
    "root_cause": (("root cause", "root-cause", "cause", "caused", "diagnos"), ("root_cause",)),
    "similar": (("similar", "like this", "history of", "past lots", "happened before"), ("similar_lot",)),
    "equipment": (("equipment", "tool", "maintenance", "chamber", "pm "), ("equipment",)),
    "defects": (("defect", "wafer map", "pattern", "inspection"), ("defects",)),
    "risk": (("why", "risk", "risky", "low yield", "explain", "predict"), ("prediction",)),
}
INTENT_ORDER = ("recommendation", "root_cause", "similar", "equipment", "defects", "risk")


def detect_intent(question: str) -> str:
    q = f" {question.lower()} "
    for intent in INTENT_ORDER:
        if any(k in q for k in INTENTS[intent][0]):
            return intent
    return "summary"


def _s(text: str, *citations: str) -> dict:
    return {"text": text, "citations": [c for c in citations if c]}


def _pct(x) -> str:
    return "n/a" if x is None else f"{float(x) * 100:.0f}%"


def sentence_prediction(ev: EvidenceSet, lot_id: str) -> list[dict]:
    p = ev.items.get(f"pred:{lot_id}")
    if not p:
        return []
    text = (
        f"{lot_id} is {p['risk_level']} risk: the calibrated probability of low yield is {_pct(p['probability_low_yield'])} "
        f"and the predicted yield is {p['predicted_yield']}%"
    )
    if p.get("actual_yield") is not None:
        text += f" (measured yield {p['actual_yield']:.1f}%)"
    return [_s(text + ".", p["id"])]


def sentence_attributions(ev: EvidenceSet) -> list[dict]:
    items = sorted(ev.of_type("attribution"), key=lambda i: -abs(i["shap_value"]))[:3]
    if not items:
        return []
    parts = [f"{i['feature']} ({'+' if i['shap_value'] >= 0 else ''}{i['shap_value']:.3f})" for i in items]
    return [_s("The model's largest drivers for this prediction are " + ", ".join(parts) + " (SHAP attributions, not physical causes).", *[i["id"] for i in items])]


def sentence_root_cause(ev: EvidenceSet, detailed: bool = False) -> list[dict]:
    items = ev.of_type("root_cause")
    if not items:
        return []
    top = items[0]
    out = [
        _s(
            f"The top-ranked root-cause candidate is {top['candidate']} (score {top['score']:.2f}, {top['confidence']} confidence) — "
            "a ranked candidate with evidence, not a confirmed cause.",
            top["id"],
        )
    ]
    if detailed and top.get("top_evidence_label"):
        out.append(_s(f"Its strongest supporting evidence: {top['top_evidence_label']}", top["id"]))
    if len(items) > 1:
        rest = ", ".join(f"{i['candidate']} ({i['score']:.2f})" for i in items[1:])
        out.append(_s(f"Other candidates: {rest}.", *[i["id"] for i in items[1:]]))
    return out


def sentence_events(ev: EvidenceSet) -> list[dict]:
    items = ev.of_type("event")
    if not items:
        return []
    parts = []
    for i in items:
        what = i.get("parameter") or i.get("alarm") or ""
        parts.append(f"{i['event_type'].lower().replace('_', ' ')}{' (' + what + ')' if what else ''}")
    return [_s(f"During the run the timeline logged: {'; '.join(parts)}.", *[i["id"] for i in items])]


def sentence_equipment(ev: EvidenceSet, equipment_id: str) -> list[dict]:
    eq = ev.items.get(f"eq:{equipment_id}")
    if not eq:
        return []
    maint = ev.of_type("maintenance")
    if eq.get("days_since_maintenance") is not None and maint:
        text = (
            f"{equipment_id} was last maintained {eq['days_since_maintenance']:.0f} days before this lot "
            f"({maint[0]['event_type']}); its recorded health score is {eq['health_score']}."
        )
        return [_s(text, eq["id"], maint[0]["id"])]
    return [_s(f"{equipment_id} has no maintenance recorded before this lot; its recorded health score is {eq['health_score']}.", eq["id"])]


def sentence_defects(ev: EvidenceSet, lot_id: str) -> list[dict]:
    d = ev.items.get(f"defect:{lot_id}")
    if not d:
        return []
    pattern = d.get("pattern") or "no"
    density = d.get("defect_density")
    return [_s(f"Inspection shows a {pattern} pattern with defect density {density:.3f} (synthetic inspection data).", d["id"])]


def sentence_similar(ev: EvidenceSet) -> list[dict]:
    items = ev.of_type("similar_lot")
    if not items:
        return []
    n_low = sum(1 for i in items if i["low_yield"])
    listing = ", ".join(f"{i['lot_id']} ({i['lot_yield']:.1f}%)" for i in items if i.get("lot_yield") is not None)
    return [_s(f"{n_low} of the {len(items)} most similar historical lots ended low-yield: {listing}.", *[i["id"] for i in items])]


def sentence_recommendations(ev: EvidenceSet) -> list[dict]:
    out = []
    for i in ev.of_type("recommendation"):
        text = f"Suggested action: {i['text']} In the synthetic intervention history, {i['recovery_text']} similar past cases recovered"
        if i.get("avg_yield_change") is not None:
            text += f" (average yield change {i['avg_yield_change']:+.1f} pp)"
        text += "."
        if i.get("small_sample_warning"):
            text += f" {i['small_sample_warning']}"
        out.append(_s(text, i["id"]))
    return out


def sentence_docs(ev: EvidenceSet) -> list[dict]:
    return [_s(f"From {i['source']} ({i['title']}): {i['excerpt']}", i["id"]) for i in ev.of_type("doc")]


def compose(intent: str, ev: EvidenceSet, lot: dict | None) -> tuple[list[dict], bool]:
    """Returns (sentences, insufficient_evidence)."""
    if lot is None:
        docs = sentence_docs(ev)
        if docs:
            return docs, False
        return [_s(f"{NOT_ENOUGH} to answer: no lot was identified in the question and no documentation section matched.")], True

    required = INTENTS.get(intent, ((), ("prediction",)))[1]
    missing = [r for r in required if not ev.has(r)]
    if missing:
        reasons = "; ".join(ev.missing.values()) or ", ".join(missing)
        return [_s(f"{NOT_ENOUGH} to answer this for {lot['lot_id']}: {reasons}.")], True

    lot_id, equipment = lot["lot_id"], lot["equipment"]
    builders = {
        "risk": lambda: sentence_prediction(ev, lot_id) + sentence_attributions(ev) + sentence_root_cause(ev)
        + sentence_events(ev) + sentence_equipment(ev, equipment) + sentence_defects(ev, lot_id) + sentence_similar(ev)
        + sentence_recommendations(ev)[:1],
        "root_cause": lambda: sentence_root_cause(ev, detailed=True) + sentence_events(ev) + sentence_defects(ev, lot_id)
        + sentence_equipment(ev, equipment),
        "recommendation": lambda: sentence_recommendations(ev) + sentence_root_cause(ev)[:1],
        "similar": lambda: sentence_similar(ev) + sentence_prediction(ev, lot_id),
        "equipment": lambda: sentence_equipment(ev, equipment) + sentence_events(ev),
        "defects": lambda: sentence_defects(ev, lot_id) + sentence_root_cause(ev)[:1],
        "summary": lambda: sentence_prediction(ev, lot_id) + sentence_root_cause(ev)[:1] + sentence_recommendations(ev)[:1],
    }
    sentences = builders[intent]() + sentence_docs(ev)
    if not sentences:
        return [_s(f"{NOT_ENOUGH} to answer this for {lot_id}.")], True
    return sentences, False


LOT_PATTERN = re.compile(r"\b(?:LOT[_-]?|L)(\d{3,12})\b", re.IGNORECASE)


def extract_lot_id(question: str) -> str | None:
    """'LOT_10123', 'lot-10123', 'L10123' -> 'LOT_10123'."""
    match = LOT_PATTERN.search(question or "")
    return f"LOT_{match.group(1)}" if match else None


def render(sentences: list[dict]) -> str:
    """Plain-text answer with bracketed citations after each sentence."""
    return " ".join(
        s["text"] + (" [" + ", ".join(s["citations"]) + "]" if s["citations"] else "") for s in sentences
    )
