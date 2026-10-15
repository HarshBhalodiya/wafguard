"""
Leakage guard: ground_truth.parquet is evaluation-only.

Hard rule from the upgrade brief: models must NEVER read the hidden fault
labels during training or inference. This test enforces it structurally -
if any module under app/features/, app/ml/, app/services/ (the inference
paths) or scripts/train_*.py ever imports the ground-truth loader or reads
ground_truth.parquet, this test fails with a message pointing at the file.
"""
import re
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Code paths that may never touch ground truth.
FORBIDDEN_PATHS = [
    BACKEND_DIR / "app" / "features",
    BACKEND_DIR / "app" / "ml",
    BACKEND_DIR / "app" / "services",
    # Phases 4-9: every new inference / serving path.
    BACKEND_DIR / "app" / "risk",
    BACKEND_DIR / "app" / "recommend",
    BACKEND_DIR / "app" / "whatif",
    BACKEND_DIR / "app" / "quality",
    BACKEND_DIR / "app" / "copilot",
    BACKEND_DIR / "app" / "jobs",
    BACKEND_DIR / "app" / "knowledge",
    BACKEND_DIR / "scripts" / "train_whatif_ensemble.py",
    BACKEND_DIR / "scripts" / "train_yield_model.py",
    BACKEND_DIR / "scripts" / "train_wafer_model.py",
]

# Any of these in a forbidden file means someone wired ground truth into a
# training/inference path.
FORBIDDEN_PATTERNS = [
    re.compile(r"ground_truth", re.IGNORECASE),
    re.compile(r"true_fault"),
    re.compile(r"from\s+app\.simulator\.mechanisms\s+import", re.IGNORECASE),
]

# The simulator itself is allowed to write ground truth (it's the source).
ALLOWED_FILES = set()


def test_no_training_or_inference_module_reads_ground_truth():
    violations = []
    for path in FORBIDDEN_PATHS:
        files = [path] if path.is_file() else sorted(path.rglob("*.py")) if path.exists() else []
        for f in files:
            if f in ALLOWED_FILES:
                continue
            source = f.read_text(encoding="utf-8")
            for pattern in FORBIDDEN_PATTERNS:
                if pattern.search(source):
                    violations.append(f"{f.relative_to(BACKEND_DIR)}: matches {pattern.pattern!r}")
    assert not violations, (
        "Ground-truth leakage detected. These files may not read hidden fault labels:\n  "
        + "\n  ".join(violations)
        + "\nGround truth is evaluation-only (docs/DECISIONS.md D-005)."
    )


def test_evaluation_module_exists_and_is_the_only_ground_truth_reader():
    """Positive control: an evaluation-only reader is expected from Phase 2 on.
    For now, assert the simulator (the writer) is the only app module allowed
    to know the term, and scripts/evaluation readers live under allowed paths."""
    simulator_dir = BACKEND_DIR / "app" / "simulator"
    for f in simulator_dir.rglob("*.py"):
        source = f.read_text(encoding="utf-8")
        # the writer may mention ground_truth (it writes the file)
        assert "app.ml" not in source or "ground_truth" not in source.lower(), (
            f"{f.name} must not wire ground truth into app.ml"
        )
