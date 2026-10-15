"""
Model warm-up (Phase 9): load every model artifact ONCE at API startup.

The services already cache bundles per process (yield_service, defect_service,
the what-if ensemble loader); calling them here moves the first-request cost
into startup and reports — per artifact — what is loaded, so /api/health can
say which features are serviceable. A missing artifact is reported, never fatal:
the API still starts and the affected endpoints return a 503 with a fix hint.
"""
import time

from app.utils.logging import get_logger

logger = get_logger(__name__)


def _try(name: str, fn) -> dict:
    start = time.perf_counter()
    try:
        detail = fn()
        return {"name": name, "loaded": True, "ms": round((time.perf_counter() - start) * 1000, 1), "detail": detail}
    except Exception as exc:  # noqa: BLE001 - warm-up reports, never crashes startup
        return {"name": name, "loaded": False, "ms": round((time.perf_counter() - start) * 1000, 1), "detail": str(exc)[:200]}


def warm_models() -> dict:
    from app.config import get_settings
    from app.copilot import docs_index
    from app.knowledge import load_engineering_kb
    from app.services import defect_service, yield_service
    from app.whatif import counterfactual
    from app.whatif.ensemble import load_ensemble

    settings = get_settings()

    def _ensemble():
        bundle = load_ensemble(settings.resolve_path(settings.model_dir))
        if bundle is None:
            raise FileNotFoundError("whatif_ensemble not trained (scripts/train_whatif_ensemble.py)")
        return f"{len(bundle.members)} members"

    results = [
        _try("lot_yield_regressor", lambda: yield_service.get_bundle(yield_service.MODEL_B_REGRESSOR)["version"]),
        _try("lot_yield_classifier", lambda: yield_service.get_bundle(yield_service.MODEL_B_CLASSIFIER)["version"]),
        _try("wafer_pattern_cnn", lambda: defect_service.get_wafer_bundle().get("version")),
        _try("whatif_ensemble", _ensemble),
        _try("engineering_kb", lambda: load_engineering_kb().get("version")),
        _try("whatif_limits", lambda: counterfactual.load_limits().get("version")),
        _try("docs_index", lambda: f"{len(docs_index.load_sections())} sections"),
    ]
    summary = {"models": results, "n_loaded": sum(r["loaded"] for r in results), "n_total": len(results)}
    logger.info("Model warm-up: %d/%d loaded (%s)", summary["n_loaded"], summary["n_total"],
                ", ".join(f"{r['name']}={'ok' if r['loaded'] else 'missing'}" for r in results))
    return summary
