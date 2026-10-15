"""
Tiny section-level retriever over the project's methodology docs (docs/*.md).

Each `## heading` section is one retrievable unit with the id
`doc:<file>#<heading-slug>`. Scoring is plain token overlap (lower-cased words
minus stopwords, weighted by inverse section frequency) — enough to find "how
is risk computed" in risk_and_rootcause_methodology.md without adding a
vector-store dependency. Phase plans and the audit are excluded: they describe
work history, not how the product behaves.
"""
from __future__ import annotations

import math
import re
from functools import lru_cache
from pathlib import Path

from app.config import PROJECT_ROOT

DOCS_DIR = PROJECT_ROOT / "docs"
EXCLUDED_PREFIXES = ("phase", "AUDIT")
MIN_SCORE = 1.5
STOPWORDS = set(
    "a an the is are was were be been of to in on for and or not it its this that with as by at from how why what "
    "which who when do does did can could should would will i we you my our your lot lots about into than then there "
    "so if any all me us is".split()
)
_WORD = re.compile(r"[a-z][a-z0-9_-]+")


def _tokens(text: str) -> list[str]:
    return [t for t in _WORD.findall(text.lower()) if t not in STOPWORDS and len(t) > 2]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


@lru_cache(maxsize=1)
def load_sections() -> list[dict]:
    sections: list[dict] = []
    if not DOCS_DIR.exists():
        return sections
    for path in sorted(DOCS_DIR.glob("*.md")):
        if path.name.startswith(EXCLUDED_PREFIXES):
            continue
        current = {"heading": path.stem, "lines": []}
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("## "):
                if current["lines"]:
                    sections.append(_finish(path, current))
                current = {"heading": line[3:].strip(), "lines": []}
            else:
                current["lines"].append(line)
        if current["lines"]:
            sections.append(_finish(path, current))
    return sections


def _finish(path: Path, current: dict) -> dict:
    body = "\n".join(current["lines"]).strip()
    return {
        "id": f"doc:{path.name}#{_slug(current['heading'])}",
        "file": f"docs/{path.name}",
        "heading": current["heading"],
        "text": body,
        "tokens": set(_tokens(current["heading"] + " " + body)),
    }


def search(question: str, k: int = 2) -> list[dict]:
    """Top-k sections by IDF-weighted token overlap (only scores >= MIN_SCORE)."""
    sections = load_sections()
    query = set(_tokens(question))
    if not sections or not query:
        return []
    n = len(sections)
    df = {t: sum(1 for s in sections if t in s["tokens"]) for t in query}
    scored = []
    for section in sections:
        score = sum(math.log(1 + n / df[t]) for t in query if t in section["tokens"])
        if score >= MIN_SCORE:
            scored.append((score, section))
    scored.sort(key=lambda pair: -pair[0])
    return [{**s, "score": round(score, 3)} for score, s in scored[:k]]


def first_sentence(text: str, max_chars: int = 220) -> str:
    """A short, quotable summary of a section: its first non-heading sentence."""
    for line in text.splitlines():
        line = line.strip(" >*-#`")
        if len(line) > 30 and not line.startswith(("|", "```")):
            sentence = re.split(r"(?<=[.!?])\s", line)[0]
            return sentence[:max_chars]
    return text[:max_chars]
