"""
Swappable LLM phrasing providers behind ONE interface.

    class LLMProvider:
        name: str
        def rephrase(question, sentences, evidence) -> list[{"text", "citations"}]

A provider receives the deterministic, already-cited draft plus the evidence
items, and may only rewrite the wording. Its output is VALIDATED before use
(`validate_rephrasing`): JSON list of sentences, every citation must be an id
present in the evidence, every sentence that states a number must cite
something, and no number may appear that is absent from the draft/evidence.
Anything else is rejected and the caller falls back to the deterministic draft.

Built-in providers: `anthropic`, `openai_compatible` (any OpenAI-style
/chat/completions endpoint incl. self-hosted or watsonx-compatible gateways via
LLM_BASE_URL). Register another with `register_provider`.
"""
from __future__ import annotations

import json
import re
from typing import Protocol

import requests

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
SYSTEM_PROMPT = (
    "You rewrite a drafted answer for a semiconductor process engineer. You receive DRAFT sentences, each with "
    "citation ids, and the EVIDENCE items those ids refer to. Rewrite for clarity only. Rules: never add a fact, "
    "number, cause or action that is not in the draft; keep every sentence's citations (ids from EVIDENCE only); "
    "call root causes 'candidates'. Reply with ONLY a JSON array of objects {\"text\": str, \"citations\": [str]}."
)
_NUMBER = re.compile(r"(?<![A-Za-z_])-?\d+(?:\.\d+)?")


class LLMProvider(Protocol):
    name: str

    def rephrase(self, question: str, sentences: list[dict], evidence: list[dict]) -> list[dict]: ...


def _user_message(question: str, sentences: list[dict], evidence: list[dict]) -> str:
    return json.dumps({"question": question, "draft": sentences, "evidence": evidence}, default=str)


def _parse_sentences(text: str) -> list[dict]:
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < start:
        raise ValueError("provider did not return a JSON array")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, list):
        raise ValueError("provider output is not a list")
    return [{"text": str(item["text"]), "citations": [str(c) for c in item.get("citations", [])]} for item in data]


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str = "", timeout: float = 15.0):
        self.api_key, self.model, self.timeout = api_key, model or "claude-haiku-5-5", timeout

    def rephrase(self, question: str, sentences: list[dict], evidence: list[dict]) -> list[dict]:
        response = requests.post(
            ANTHROPIC_MESSAGES_URL,
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={
                "model": self.model,
                "max_tokens": 800,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": _user_message(question, sentences, evidence)}],
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        text = "".join(b.get("text", "") for b in response.json().get("content", []) if b.get("type") == "text")
        return _parse_sentences(text)


class OpenAICompatibleProvider:
    name = "openai_compatible"

    def __init__(self, api_key: str, model: str = "", base_url: str = "", timeout: float = 15.0):
        self.api_key, self.model, self.timeout = api_key, model or "gpt-4o-mini", timeout
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")

    def rephrase(self, question: str, sentences: list[dict], evidence: list[dict]) -> list[dict]:
        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers={"authorization": f"Bearer {self.api_key}", "content-type": "application/json"},
            json={
                "model": self.model,
                "max_tokens": 800,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": _user_message(question, sentences, evidence)},
                ],
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        choices = response.json().get("choices", [])
        return _parse_sentences(choices[0].get("message", {}).get("content", "") if choices else "")


_FACTORIES = {
    "anthropic": lambda s: AnthropicProvider(s.llm_api_key, s.llm_model),
    "openai_compatible": lambda s: OpenAICompatibleProvider(s.llm_api_key, s.llm_model, s.llm_base_url),
}


def register_provider(name: str, factory) -> None:
    """Add/replace a provider: `factory(settings) -> LLMProvider`."""
    _FACTORIES[name] = factory


def provider_from_settings(settings) -> LLMProvider | None:
    name = (settings.llm_provider or "").strip().lower()
    if not name or name not in _FACTORIES or (name in ("anthropic", "openai_compatible") and not settings.llm_api_key):
        return None
    return _FACTORIES[name](settings)


def _numbers(text: str) -> set[str]:
    return {n.lstrip("-").rstrip("0").rstrip(".") or "0" for n in _NUMBER.findall(text)}


def validate_rephrasing(rephrased: list[dict], draft: list[dict], evidence_ids: set[str]) -> tuple[bool, str | None]:
    """Accept only output that cites existing evidence and invents no number."""
    if not rephrased:
        return False, "empty output"
    allowed_numbers = set().union(*(_numbers(s["text"]) for s in draft)) | set().union(*(_numbers(i) for i in evidence_ids))
    for sentence in rephrased:
        unknown = [c for c in sentence["citations"] if c not in evidence_ids]
        if unknown:
            return False, f"cites unknown evidence {unknown}"
        nums = _numbers(sentence["text"])
        if nums and not sentence["citations"]:
            return False, "a sentence states a number without citing evidence"
        invented = nums - allowed_numbers
        if invented:
            return False, f"introduces numbers not in the draft: {sorted(invented)}"
    draft_citations = {c for s in draft for c in s["citations"]}
    kept = {c for s in rephrased for c in s["citations"]}
    if draft_citations and not draft_citations <= kept:
        return False, f"drops citations {sorted(draft_citations - kept)}"
    return True, None
