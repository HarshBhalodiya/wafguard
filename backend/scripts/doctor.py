"""
Preflight diagnostics (Phase 6, P6-4): one command that says whether this
checkout can actually run, and what to do about it if not.

    cd backend
    .venv/Scripts/python.exe scripts/doctor.py

Checks, in order (OK / WARN / FAIL per line):
  1. Environment      — .env loaded, JWT secret left at the dev default,
                        optional LLM provider configured coherently.
  2. MongoDB          — reachable (redacted URI, short timeout); index state.
  3. Dataset          — data/synthetic/ files + config fingerprint.
  4. Model artifacts  — trained bundles resolvable via their latest pointers,
                        evidence table, temporal/vision embeddings.

Exit codes: 0 = all OK (warnings allowed), 1 = at least one FAIL.

The detailed checks reuse `app.ops.readiness` — the same functions that back
`/api/health` — so the doctor, the health endpoint, and the docs can never
drift apart. Nothing here writes to the database or the disk.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import get_settings  # noqa: E402
from app.database import mongodb  # noqa: E402
from app.ops.readiness import collect_readiness  # noqa: E402

OK, WARN, FAIL = "OK", "WARN", "FAIL"
DEV_SECRET = "dev-only-insecure-secret-change-me"


def _line(status: str, name: str, detail: str) -> tuple[str, str, str]:
    return (status, name, detail)


def check_environment(settings) -> list[tuple[str, str, str]]:
    results = []
    env_file = Path(settings.model_config.get("env_file", ""))
    if env_file.exists():
        results.append(_line(OK, "env file", f"loaded {env_file.name}"))
    else:
        results.append(_line(WARN, "env file", "no .env found — defaults + process env apply (see .env.example)"))

    if settings.jwt_secret_key == DEV_SECRET:
        results.append(_line(WARN, "jwt secret", "dev default in use — fine locally, change before any shared deployment"))
    else:
        results.append(_line(OK, "jwt secret", "custom value configured"))

    if settings.llm_provider and not settings.llm_api_key:
        results.append(_line(WARN, "llm provider", f"provider {settings.llm_provider!r} configured without LLM_API_KEY — copilot falls back to the deterministic template"))
    else:
        results.append(_line(OK, "llm provider", f"{settings.llm_provider or 'none (deterministic fallback)'}"))

    if not settings.alert_dispatch_enabled:
        results.append(_line(WARN, "alert dispatch", "disabled — raised alerts are recorded but never delivered"))
    return results


def check_mongo(settings) -> list[tuple[str, str, str]]:
    mongodb.connect(timeout_ms=3_000)
    if not mongodb.is_connected():
        return [_line(FAIL, "mongodb", f"unreachable at the configured URI — start MongoDB or fix MONGODB_URI, then run scripts/seed_database.py")]
    results = [_line(OK, "mongodb", f"connected (db={settings.mongodb_database})")]
    try:
        db = mongodb.get_database()
        missing_indexes = [name for name in ("lots", "predictions", "timeline_events") if list(db[name].list_indexes()) == ["_id_"]]
        if missing_indexes:
            results.append(_line(WARN, "indexes", f"no compound indexes on {missing_indexes} — run scripts/seed_database.py to ensure them"))
        else:
            results.append(_line(OK, "indexes", "expected indexes present"))
    except Exception as exc:  # noqa: BLE001 - doctor reports, never crashes
        results.append(_line(WARN, "indexes", f"could not inspect: {exc.__class__.__name__}"))
    return results


def check_data_and_artifacts(settings) -> list[tuple[str, str, str]]:
    full = collect_readiness(settings)
    results = []

    dataset = full["dataset"]
    if dataset["present"]:
        results.append(_line(OK, "dataset", f"schema v{dataset['schema_version']}, fingerprint {dataset['config_hash']}"))
    else:
        results.append(_line(FAIL, "dataset", dataset.get("fix", "python scripts/build_dataset.py")))

    for name, entry in full["bundles"].items():
        if entry["present"]:
            results.append(_line(OK, f"bundle {name}", f"v{entry['version']}"))
        elif name in ("lot_yield_regressor", "lot_yield_classifier"):
            results.append(_line(FAIL, f"bundle {name}", "missing — run 'python scripts/build_dataset.py --all' (or scripts/train_yield_model.py)"))
        else:
            results.append(_line(WARN, f"bundle {name}", "missing — vision/SECOM endpoints will 503 until scripts/train_wafer_model.py (or download_secom.py + train) runs"))

    evidence = full["evidence_table"]
    results.append(_line(OK if evidence["present"] else WARN, "evidence table",
                         "present" if evidence["present"] else f"missing — pattern evidence scores 0 ({evidence['fix']})"))

    for space, entry in full["embeddings"].items():
        results.append(_line(OK if entry["present"] else WARN, f"{space} embeddings",
                             "present" if entry["present"] else f"missing — {space} similarity space unavailable ({entry['fix']})"))
    return results


def run_doctor(settings=None) -> list[tuple[str, str, str]]:
    settings = settings or get_settings()
    results = []
    results += check_environment(settings)
    results += check_mongo(settings)
    results += check_data_and_artifacts(settings)
    return results


def main() -> None:
    results = run_doctor()
    width = max(len(name) for _, name, _ in results)
    for status, name, detail in results:
        print(f"[{status:4s}] {name:<{width}s}  {detail}")

    failures = [r for r in results if r[0] == FAIL]
    warnings = [r for r in results if r[0] == WARN]
    print(f"\n{len(results)} checks: {len(failures)} FAIL, {len(warnings)} WARN")
    if failures:
        print("Fix the FAIL items above, then re-run scripts/doctor.py.")
        raise SystemExit(1)
    print("Environment is ready to run the app (uvicorn app.main:app + npm run dev).")


if __name__ == "__main__":
    main()
