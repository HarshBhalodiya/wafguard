"""Authored engineering knowledge (action text only — never outcome numbers)."""
from functools import lru_cache
from pathlib import Path

import yaml

KB_FILE = Path(__file__).resolve().parent / "engineering_kb.yaml"


@lru_cache(maxsize=1)
def load_engineering_kb() -> dict:
    """Parse engineering_kb.yaml once. Returns the raw mapping (version, causes, ...)."""
    with open(KB_FILE, encoding="utf-8") as f:
        kb = yaml.safe_load(f)
    if not isinstance(kb, dict) or "causes" not in kb:
        raise ValueError(f"{KB_FILE} must define a top-level 'causes' mapping")
    return kb


def actions_for(cause: str) -> list[dict]:
    """The authored actions for one root-cause candidate ([] when the KB has none)."""
    return list(load_engineering_kb()["causes"].get(cause, []))


def all_actions() -> dict[str, dict]:
    """action_id -> {id, text, kind, cause} across the whole KB."""
    out: dict[str, dict] = {}
    for cause, actions in load_engineering_kb()["causes"].items():
        for action in actions:
            out[action["id"]] = {**action, "cause": cause}
    return out
