"""
Phase 6 tests (P6-2): the one-command pipeline's stage resolution and
fail-stop semantics, the API metadata smoke test the Phase 2 report promised
("the API serves the fresh metadata version"), and the readiness contract
backing /api/health.

Everything here is hermetic: the pipeline tests inject fake runners (no
training, no Mongo), and the readiness tests run against tmp dirs.
"""
import json

import pytest

import build_dataset
from app.main import app
from app.ops.readiness import bundle_readiness, dataset_readiness, shallow_readiness


# ---------------------------------------------------------------------------
# P6-1: stage resolution (pure function — the audit's generate→seed→train→evaluate)
# ---------------------------------------------------------------------------
def test_default_invocation_is_generate_only():
    """Back-compat: no flags means generation only, exactly as before."""
    assert build_dataset.resolve_stages() == ["generate"]


def test_all_runs_the_full_chain_in_canonical_order():
    assert build_dataset.resolve_stages(all_=True) == ["generate", "seed", "train", "evaluate"]


def test_stages_subset_is_reordered_canonically():
    assert build_dataset.resolve_stages(stages="evaluate,generate") == ["generate", "evaluate"]


def test_from_resumes_inclusively():
    assert build_dataset.resolve_stages(all_=True, from_stage="seed") == ["seed", "train", "evaluate"]
    assert build_dataset.resolve_stages(from_stage="evaluate") == ["evaluate"]


def test_unknown_stage_is_rejected_not_dropped():
    with pytest.raises(ValueError, match="unknown stage"):
        build_dataset.resolve_stages(stages="generate,bogus")
    with pytest.raises(ValueError, match="unknown --from"):
        build_dataset.resolve_stages(from_stage="bogus")


def test_all_and_stages_are_mutually_exclusive():
    with pytest.raises(ValueError, match="not both"):
        build_dataset.resolve_stages(stages="generate", all_=True)


# ---------------------------------------------------------------------------
# P6-1: runner semantics — ordering, fail-stop, resume exit code
# ---------------------------------------------------------------------------
def _recording_runners(calls, fail_at=None):
    def make(name):
        def runner():
            calls.append(name)
            if name == fail_at:
                raise RuntimeError(f"{name} exploded")
        return runner

    return {stage: make(stage) for stage in build_dataset.STAGES}


def test_runner_executes_stages_in_order_and_returns_zero():
    calls = []
    code = build_dataset.run_pipeline(["generate", "seed", "train", "evaluate"],
                                      runners=_recording_runners(calls))
    assert code == 0
    assert calls == ["generate", "seed", "train", "evaluate"]


def test_runner_stops_at_first_failure_with_nonzero_exit():
    calls = []
    code = build_dataset.run_pipeline(["generate", "seed", "train"], runners=_recording_runners(calls, fail_at="seed"))
    assert code == 1
    assert calls == ["generate", "seed"], "train must never run after seed failed"


# ---------------------------------------------------------------------------
# P6-2: the metadata smoke test the Phase 2 report asked for
# ---------------------------------------------------------------------------
@pytest.fixture()
def client(db):
    from fastapi.testclient import TestClient

    from app.api.deps import get_db

    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        login = test_client.post("/api/auth/login", json={"username": "admin", "password": "wafguard-admin"})
        assert login.status_code == 200, login.text
        test_client.headers["Authorization"] = f"Bearer {login.json()['data']['access_token']}"
        yield test_client
    app.dependency_overrides.clear()


def test_api_serves_the_fresh_model_metadata_version(client):
    """Retrain->backfill->serve chain: /api/models must report exactly the
    version the training script stamps (MODEL_VERSION), so a stale
    model_versions collection can never masquerade as fresh."""
    import train_yield_model as tym

    response = client.get("/api/models")
    assert response.status_code == 200, response.text
    models = {m["model_name"]: m["version"] for m in response.json()["data"]}
    assert models["lot_yield_regressor"] == tym.MODEL_VERSION
    assert models["lot_yield_classifier"] == tym.MODEL_VERSION


def test_health_reports_readiness_block(client):
    """P6-3: /api/health is no longer a DB-only green light."""
    data = client.get("/api/health").json()["data"]
    readiness = data["readiness"]
    for key in ("dataset_present", "dataset_fingerprint", "tabular_models_ready",
                "model_versions", "evidence_table_present"):
        assert key in readiness
    # with the conftest-trained bundles, the app is genuinely serviceable
    assert data["status"] in ("healthy", "degraded")
    if data["database"] == "connected":
        assert data["status"] == ("healthy" if readiness["tabular_models_ready"] else "degraded")


def test_shallow_readiness_leaks_no_filesystem_paths(settings):
    """The health route is unauthenticated — the readiness view must stay
    booleans/version strings/fingerprint, never a filesystem path."""
    payload = json.dumps(shallow_readiness(settings))
    assert str(settings.resolve_path(settings.model_dir)) not in payload
    assert "fix" not in payload, "fix commands live in the doctor, not the public health view"


# ---------------------------------------------------------------------------
# P6-3: readiness primitives against tmp dirs
# ---------------------------------------------------------------------------
def test_dataset_readiness_reports_missing_dataset_with_fix(tmp_path):
    result = dataset_readiness(tmp_path / "nope")
    assert result["present"] is False
    assert "build_dataset" in result["fix"]


def test_dataset_readiness_reads_fingerprint(tmp_path):
    (tmp_path / "_generation_metadata.json").write_text(
        json.dumps({"schema_version": 2, "config_hash": "deadbeef"}), encoding="utf-8"
    )
    result = dataset_readiness(tmp_path)
    assert result == {"present": True, "schema_version": 2, "config_hash": "deadbeef"}


def test_bundle_readiness_resolves_latest_pointer(tmp_path):
    (tmp_path / "lot_yield_classifier.latest.json").write_text(
        json.dumps({"version": "2.0.0", "artifact_file": "lot_yield_classifier_v2.0.0.joblib"}),
        encoding="utf-8",
    )
    missing = bundle_readiness(tmp_path, "lot_yield_classifier")
    assert missing == {"present": False, "version": "2.0.0"}, "pointer without artifact is not ready"

    (tmp_path / "lot_yield_classifier_v2.0.0.joblib").write_bytes(b"stub")
    present = bundle_readiness(tmp_path, "lot_yield_classifier")
    assert present == {"present": True, "version": "2.0.0"}


def test_corrupt_latest_pointer_degrades_honestly(tmp_path):
    (tmp_path / "lot_yield_regressor.latest.json").write_text("{not json", encoding="utf-8")
    assert bundle_readiness(tmp_path, "lot_yield_regressor") == {"present": False, "version": None}
