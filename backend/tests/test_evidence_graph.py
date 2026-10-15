"""
Phase 3C tests — the root-cause evidence graph.

Checks the contract the brief asks for: nodes/edges with strength and
evidence links, rules read from a file an engineer can read, and no
invented links (a candidate with no rule gets no mechanism edge).
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.main import app
from app.rootcause import evidence_graph
from app.rootcause.evidence_graph import load_rules
from app.services import rootcause_service
from app.utils.exceptions import NotFoundError

ALLOWED_NODE_TYPES = {"equipment", "chamber", "sensor_issue", "mechanism", "defect_pattern", "yield_loss"}
ALLOWED_EDGE_KINDS = {"measured", "rule"}


@pytest.fixture()
def client(db):
    """Authenticated admin client (Phase 4 RBAC needs a token on every route)."""
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        login = test_client.post("/api/auth/login", json={"username": "admin", "password": "wafguard-admin"})
        assert login.status_code == 200, login.text
        test_client.headers["Authorization"] = f"Bearer {login.json()['data']['access_token']}"
        yield test_client
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------- structure
def test_graph_nodes_and_edges_have_the_documented_shape(db, incident_lot_id):
    graph = evidence_graph.build_evidence_graph(db, incident_lot_id)
    assert graph["lot_id"] == incident_lot_id
    assert graph["nodes"], "a scored faulty lot must produce at least one node"
    assert graph["rules_version"] == load_rules()["rules_version"]
    assert graph["rules_source"].endswith("graph_rules.json")
    assert graph["disclaimer"]

    for node in graph["nodes"]:
        assert node["type"] in ALLOWED_NODE_TYPES, f"unexpected node type {node['type']!r}"
        assert node["source"] in {"measured", "rule"}
        assert node["label"] and node["detail"]
        assert node["evidence_ids"], f"node {node['id']} carries no evidence link"

    ids = [node["id"] for node in graph["nodes"]]
    assert len(ids) == len(set(ids)), "node ids must be unique"


def test_every_edge_has_strength_kind_and_resolvable_evidence(db, incident_lot_id):
    root_cause = rootcause_service.compute_root_cause(db, incident_lot_id)
    graph = evidence_graph.build_evidence_graph(db, incident_lot_id, root_cause=root_cause)

    known_evidence = {
        item["evidence_id"]
        for candidate in root_cause["candidates"]
        for item in candidate["evidence"]
    }
    known_rule_ids = {rule["rule_id"] for rule in load_rules()["candidate_rules"]}
    lot_evidence_prefix = f"lot:{incident_lot_id}:"

    for edge in graph["edges"]:
        assert 0.0 <= edge["strength"] <= 1.0, f"edge {edge['id']} strength out of range"
        assert edge["kind"] in ALLOWED_EDGE_KINDS
        assert edge["evidence_ids"], f"edge {edge['id']} has no evidence link"
        for evidence_id in edge["evidence_ids"]:
            ok = (
                evidence_id in known_evidence
                or evidence_id in known_rule_ids
                or evidence_id.startswith(lot_evidence_prefix)
            )
            assert ok, f"edge {edge['id']} cites unknown evidence {evidence_id!r}"


def test_edges_only_reference_component_nodes(db, incident_lot_id):
    graph = evidence_graph.build_evidence_graph(db, incident_lot_id)
    node_ids = {node["id"] for node in graph["nodes"]}
    for edge in graph["edges"]:
        assert edge["source"] in node_ids, f"edge {edge['id']} source is not a node"
        assert edge["target"] in node_ids, f"edge {edge['id']} target is not a node"


def test_rule_edges_cite_a_rule_id_that_exists_in_the_file(db, incident_lot_id):
    graph = evidence_graph.build_evidence_graph(db, incident_lot_id)
    known_rule_ids = {rule["rule_id"] for rule in load_rules()["candidate_rules"]}
    rule_edges = [e for e in graph["edges"] if e["kind"] == "rule"]
    assert rule_edges, "the incident lot reaches a mechanism through a rule edge"
    for edge in rule_edges:
        assert any(eid in known_rule_ids for eid in edge["evidence_ids"]), edge["id"]


# --------------------------------------------------------------------------- honesty guarantees
def test_no_ground_truth_is_read_by_the_graph_module():
    source = Path(evidence_graph.__file__).read_text(encoding="utf-8")
    assert "ground_truth" not in source
    assert "pattern_associations" not in source
    assert "data/synthetic" not in source


def test_candidate_without_a_rule_gets_no_invented_mechanism_edge(db, incident_lot_id):
    """Voltage has no mechanism rule; the graph must show the gap, not a fake edge."""
    fake_root_cause = {
        "candidates": [
            {
                "candidate": "Voltage",
                "score": 0.42,
                "confidence": "MEDIUM",
                "signal_breakdown": {"shap_attribution": 0.4},
                "evidence": [
                    {
                        "signal": "parameter_deviation",
                        "evidence_id": f"rc:{incident_lot_id}:voltage:0",
                        "kind": "FACT",
                        "source": "process_runs",
                    }
                ],
            }
        ],
        "dominant_wafer_pattern": "None",
    }
    graph = evidence_graph.build_evidence_graph(db, incident_lot_id, root_cause=fake_root_cause)

    assert not [n for n in graph["nodes"] if n["type"] == "mechanism"]
    assert not [e for e in graph["edges"] if e["target"].startswith("mechanism:")]
    assert any("Voltage" in gap for gap in graph["gaps"]), graph["gaps"]


def test_yield_loss_edge_strength_is_measured_not_constant(db, incident_lot_id):
    from app.config import LOW_YIELD_THRESHOLD
    from app.database.repositories.lot_repository import LotRepository

    lot = LotRepository(db).get_by_lot_id(incident_lot_id)
    graph = evidence_graph.build_evidence_graph(db, incident_lot_id)
    loss_edges = [e for e in graph["edges"] if e["target"] == "yield_loss"]
    assert loss_edges, "a scored lot with a pattern must reach the yield-loss node"

    density = float(lot.get("defect_density") or 0.0)
    deficit = max(0.0, (LOW_YIELD_THRESHOLD - float(lot["yield"])) / LOW_YIELD_THRESHOLD)
    expected = round(min(1.0, max(density, deficit)), 4)
    for edge in loss_edges:
        assert edge["strength"] == expected
        assert edge["kind"] == "measured"


def test_unknown_lot_raises_not_found(db):
    with pytest.raises(NotFoundError):
        evidence_graph.build_evidence_graph(db, "LOT_DOES_NOT_EXIST")


def test_rules_file_is_valid_json_and_covers_the_engine_candidate_set():
    rules = json.loads(Path(evidence_graph.RULES_PATH).read_text(encoding="utf-8"))
    assert rules["rules_version"]
    assert rules["justification_source"]
    from app.services.rootcause_service import CANDIDATE_DEFINITIONS

    for rule in rules["candidate_rules"]:
        assert rule["candidate"] in CANDIDATE_DEFINITIONS, f"rule for unknown candidate {rule['candidate']}"
        assert rule["defect_patterns"], "a rule must name at least one defect pattern"
        assert 0.0 < float(rule["edge_strength"]) <= 1.0
        assert rule["justification"], "every rule must carry a readable justification"
    for unmapped in rules["unmapped_candidates"]:
        assert unmapped in CANDIDATE_DEFINITIONS


# --------------------------------------------------------------------------- API contract
def test_evidence_graph_endpoint_returns_the_graph(client, incident_lot_id):
    response = client.get(f"/api/lots/{incident_lot_id}/evidence-graph")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["lot_id"] == incident_lot_id
    assert data["nodes"] and data["edges"]

    response = client.get(f"/api/lots/{incident_lot_id}/evidence-graph", params={"top_n": 99})
    assert response.status_code == 422, "top_n above the cap must be rejected"


def test_evidence_graph_endpoint_requires_auth(db, incident_lot_id):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as anonymous:
        response = anonymous.get(f"/api/lots/{incident_lot_id}/evidence-graph")
        assert response.status_code == 401, response.text
    app.dependency_overrides.clear()


def test_evidence_graph_endpoint_unknown_lot_is_404(client):
    response = client.get("/api/lots/LOT_DOES_NOT_EXIST/evidence-graph")
    assert response.status_code == 404, response.text
