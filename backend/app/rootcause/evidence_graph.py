"""
Root-cause evidence graph (brief PHASE 3C).

Turns a lot's ranked root-cause candidates into a directed graph:

    equipment / chamber → sensor issue → mechanism → defect pattern → yield loss

Every node declares whether it is `measured` (from this lot's own data) or
`rule` (from `graph_rules.json`), and every edge carries a `strength` in
[0, 1] plus `evidence_ids` pointing at the evidence items that justify it.

Honesty rules this module follows (brief: "correlation is not cause"):
  * The graph shows *candidates*, never confirmed causes.
  * Edges derived from a rule are labelled `kind: "rule"` and cite their
    `rule_id` — an auditable line of JSON, not a hidden constant.
  * A candidate with no mechanism rule (Voltage, Current) gets a node and NO
    invented mechanism edge; the omission is reported in `gaps`.
  * Ground truth is never read (see app/evaluation/ for the only module that
    may look at it, evaluation-only).
"""
import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

from app.config import LOW_YIELD_THRESHOLD
from app.database.repositories.lot_repository import LotRepository
from app.services import rootcause_service
from app.utils.exceptions import NotFoundError

GRAPH_VERSION = "1.0.0"
RULES_PATH = Path(__file__).resolve().parent / "graph_rules.json"

# Edge strength for a rule link when this lot's own wafer pattern is NOT one of
# the patterns the rule predicts: the link still exists (it is the rule) but the
# lot's inspection evidence does not corroborate it, so it is discounted.
UNCORROBORATED_RULE_FACTOR = 0.4


@lru_cache(maxsize=1)
def load_rules() -> dict:
    """Read `graph_rules.json` once per process."""
    with open(RULES_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def rules_by_candidate() -> dict[str, list[dict]]:
    """candidate name → list of rule dicts (a candidate may map to one mechanism)."""
    out: dict[str, list[dict]] = {}
    for rule in load_rules().get("candidate_rules", []):
        out.setdefault(rule["candidate"], []).append(rule)
    return out


def _slug(text: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in text.lower()).strip("-")


def _evidence_ids(candidate: dict, signal: Optional[str] = None) -> list[str]:
    """Evidence ids of a candidate, optionally only those for one signal."""
    items = candidate.get("evidence", [])
    if signal is not None:
        items = [item for item in items if item.get("signal") == signal]
    return [item["evidence_id"] for item in items if item.get("evidence_id")]


def build_evidence_graph(db, lot_id: str, top_n: int = 3, root_cause: Optional[dict] = None) -> dict:
    """Build the evidence graph for one lot.

    Parameters
    ----------
    db : pymongo Database
    lot_id : the lot to explain.
    top_n : how many ranked candidates to include (default 3).
    root_cause : pre-computed root-cause result (avoids recomputation when the
        caller already has it, e.g. the API endpoint).
    """
    lot = LotRepository(db).get_by_lot_id(lot_id)
    if not lot:
        raise NotFoundError(f"Lot {lot_id} was not found.")

    if root_cause is None:
        root_cause = rootcause_service.compute_root_cause(db, lot_id)

    rules = load_rules()
    candidate_rules = rules_by_candidate()
    unmapped = rules.get("unmapped_candidates", {})

    candidates = root_cause.get("candidates", [])[: max(1, top_n)]
    observed_pattern = root_cause.get("dominant_wafer_pattern") or "None"
    lot_yield = lot.get("yield")
    defect_density = float(lot.get("defect_density") or 0.0)

    nodes: list[dict] = []
    edges: list[dict] = []
    gaps: list[str] = []
    seen_nodes: set[str] = set()

    def add_node(node: dict) -> str:
        if node["id"] not in seen_nodes:
            seen_nodes.add(node["id"])
            nodes.append(node)
        return node["id"]

    # ---------------------------------------------------------------- lot-level
    equipment_id = lot.get("equipment")
    chamber_id = lot.get("chamber_id") or lot.get("chamber")
    observed_loss = lot_yield is not None and float(lot_yield) < LOW_YIELD_THRESHOLD
    yield_node = add_node(
        {
            "id": "yield_loss",
            "type": "yield_loss",
            # Named for the brief's node type, but the label states what this lot
            # actually did — a healthy lot must not be labelled a loss.
            "label": f"Yield outcome on {lot_id}",
            "source": "measured",
            "value": None if lot_yield is None else round(float(lot_yield), 2),
            "detail": (
                f"actual lot yield {lot_yield}% — below the {LOW_YIELD_THRESHOLD}% low-yield threshold, "
                "so a yield loss was observed"
                if observed_loss
                else f"actual lot yield {lot_yield}% — at or above the {LOW_YIELD_THRESHOLD}% low-yield "
                "threshold, so no yield loss was observed on this lot (the edges below are candidate "
                "links, not a loss that happened)"
            ),
            "evidence_ids": [f"lot:{lot_id}:yield"],
        }
    )
    if equipment_id:
        add_node(
            {
                "id": f"equipment:{equipment_id}",
                "type": "equipment",
                "label": str(equipment_id),
                "source": "measured",
                "value": None,
                "detail": f"process step {lot.get('process_step', 'unknown')}",
                "evidence_ids": [f"lot:{lot_id}:equipment"],
            }
        )
    if chamber_id:
        add_node(
            {
                "id": f"chamber:{chamber_id}",
                "type": "chamber",
                "label": str(chamber_id),
                "source": "measured",
                "value": None,
                "detail": f"chamber of {equipment_id or 'unknown tool'}",
                "evidence_ids": [f"lot:{lot_id}:chamber"],
            }
        )

    pattern_nodes: dict[str, str] = {}
    mechanism_nodes: dict[str, str] = {}

    for candidate in candidates:
        name = candidate["candidate"]
        slug = _slug(name)
        score = float(candidate.get("score") or 0.0)
        candidate_evidence_ids = _evidence_ids(candidate)

        sensor_node = add_node(
            {
                "id": f"sensor_issue:{slug}",
                "type": "sensor_issue",
                "label": name,
                "source": "measured",
                "value": round(score, 4),
                "detail": f"{candidate.get('confidence', 'LOW')} confidence · score {score:.3f}",
                "evidence_ids": candidate_evidence_ids,
            }
        )

        # equipment / chamber → sensor issue (measured where the lot has equipment evidence)
        equip_signal = float((candidate.get("signal_breakdown") or {}).get("equipment_association") or 0.0)
        equip_evidence_ids = _evidence_ids(candidate, signal="equipment_association")
        for upstream in (f"equipment:{equipment_id}" if equipment_id else None, f"chamber:{chamber_id}" if chamber_id else None):
            if not upstream or upstream not in seen_nodes:
                continue
            if equip_signal <= 0.0 or not equip_evidence_ids:
                continue  # never invent an equipment link without measured evidence
            edges.append(
                {
                    "id": f"{upstream}->{sensor_node}",
                    "source": upstream,
                    "target": sensor_node,
                    "strength": round(min(1.0, equip_signal), 4),
                    "kind": "measured",
                    "evidence_ids": equip_evidence_ids,
                    "label": f"measured equipment association for {name}",
                }
            )

        candidate_rules_list = candidate_rules.get(name, [])
        if not candidate_rules_list:
            if name in unmapped:
                gaps.append(f"{name}: no mechanism rule — {unmapped[name]}")
            else:
                gaps.append(f"{name}: no mechanism rule defined in graph_rules.json.")
            continue

        for rule in candidate_rules_list:
            mechanism_id = rule["mechanism_id"]
            mechanism_node = mechanism_nodes.get(mechanism_id)
            if mechanism_node is None:
                mechanism_node = add_node(
                    {
                        "id": f"mechanism:{mechanism_id}",
                        "type": "mechanism",
                        "label": rule["mechanism"],
                        "source": "rule",
                        "value": None,
                        "detail": rule["justification"],
                        "evidence_ids": [rule["rule_id"]],
                    }
                )
                mechanism_nodes[mechanism_id] = mechanism_node

            # sensor issue → mechanism: lot-specific (candidate score) x rule strength.
            edges.append(
                {
                    "id": f"{sensor_node}->{mechanism_node}",
                    "source": sensor_node,
                    "target": mechanism_node,
                    "strength": round(min(1.0, score * float(rule["edge_strength"])), 4),
                    "kind": "rule",
                    "evidence_ids": candidate_evidence_ids + [rule["rule_id"]],
                    "label": f"candidate score {score:.3f} × rule strength {rule['edge_strength']}",
                }
            )

            for pattern in rule["defect_patterns"]:
                pattern_node = pattern_nodes.get(pattern)
                if pattern_node is None:
                    is_observed = pattern == observed_pattern
                    pattern_node = add_node(
                        {
                            "id": f"pattern:{pattern}",
                            "type": "defect_pattern",
                            "label": pattern,
                            "source": "measured" if is_observed else "rule",
                            "value": round(defect_density, 4),
                            "detail": (
                                f"observed as this lot's dominant wafer pattern "
                                f"(defect density {defect_density:.3f})"
                                if is_observed
                                else f"predicted by rule, not observed on this lot (defect density {defect_density:.3f})"
                            ),
                            "evidence_ids": _evidence_ids(candidate, signal="wafer_pattern") or [rule["rule_id"]],
                        }
                    )
                    pattern_nodes[pattern] = pattern_node

                corroborated = pattern == observed_pattern
                strength = float(rule["edge_strength"]) * (1.0 if corroborated else UNCORROBORATED_RULE_FACTOR)
                # A rule-kind edge must always cite the rule that produced it, even when
                # the lot's own inspection evidence is also attached (and vice versa).
                evidence_ids = _evidence_ids(candidate, signal="wafer_pattern")
                if rule["rule_id"] not in evidence_ids:
                    evidence_ids = evidence_ids + [rule["rule_id"]]
                edges.append(
                    {
                        "id": f"{mechanism_node}->{pattern_node}",
                        "source": mechanism_node,
                        "target": pattern_node,
                        "strength": round(min(1.0, strength), 4),
                        "kind": "measured" if corroborated else "rule",
                        "evidence_ids": evidence_ids,
                        "label": (
                            "corroborated by this lot's inspection"
                            if corroborated
                            else "predicted by rule; not corroborated by this lot's inspection"
                        ),
                    }
                )

    # ------------------------------------------------- defect pattern → yield loss
    # Strength is MEASURED: the worse of this lot's defect density and how far
    # its yield sits below the low-yield threshold. Both are lot data.
    yield_deficit = 0.0
    if lot_yield is not None:
        yield_deficit = max(0.0, (LOW_YIELD_THRESHOLD - float(lot_yield)) / LOW_YIELD_THRESHOLD)
    loss_strength = round(min(1.0, max(defect_density, yield_deficit)), 4)
    for pattern_node in pattern_nodes.values():
        edges.append(
            {
                "id": f"{pattern_node}->{yield_node}",
                "source": pattern_node,
                "target": yield_node,
                "strength": loss_strength,
                "kind": "measured",
                "evidence_ids": [f"lot:{lot_id}:yield", f"lot:{lot_id}:defect_density"],
                "label": (
                    f"measured defect density {defect_density:.3f}, "
                    f"yield {lot_yield}% vs threshold {LOW_YIELD_THRESHOLD}%"
                ),
            }
        )

    if not pattern_nodes:
        gaps.append(
            "no defect-pattern node: no candidate with a mechanism rule was actionable for this lot "
            "(the graph is not padded with an invented pattern→yield edge)."
        )
    elif observed_pattern == "None":
        gaps.append(
            "this lot has no dominant observed wafer pattern (no persisted inspection result), so every "
            "mechanism→pattern link is rule-only and falls back to the discounted rule strength — no "
            "inspection evidence corroborates it."
        )

    return {
        "graph_version": GRAPH_VERSION,
        "rules_version": rules.get("rules_version"),
        "rules_source": "backend/app/rootcause/graph_rules.json",
        "lot_id": lot_id,
        "dominant_wafer_pattern": observed_pattern,
        "nodes": nodes,
        "edges": edges,
        "gaps": gaps,
        "disclaimer": (
            "Candidate links, not confirmed causes. Edges marked kind='rule' come from the readable "
            "rules file; edges marked kind='measured' come from this lot's own data. Synthetic dataset."
        ),
    }
