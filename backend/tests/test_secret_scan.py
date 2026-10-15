"""
Phase 4 secret hygiene (P4-5): no secret-shaped string may ever be committed.
Scans every git-TRACKED file (the working tree may legitimately contain
`.env` with real dev credentials — it must stay untracked, asserted below).
"""
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Shape-only patterns — they detect the credential CLASS, never a specific value.
SECRET_PATTERNS = {
    "mongodb URI with credentials": re.compile(rb"mongodb(?:\+srv)?://[^:\s/@]+:[^@\s/]+@"),
    "anthropic-style API key": re.compile(rb"sk-ant-[A-Za-z0-9_\-]{16,}"),
    "openai-style API key": re.compile(rb"sk-(?:proj-)?[A-Za-z0-9]{20,}"),
    "slack token": re.compile(rb"xox[baprs]-[A-Za-z0-9-]{10,}"),
    "AWS access key id": re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    "Google API key": re.compile(rb"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "private key block": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
}


def _tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, check=True
    )
    return [REPO_ROOT / p.decode("utf-8") for p in result.stdout.split(b"\x00") if p]


def test_env_file_is_not_tracked():
    tracked = {p.name for p in _tracked_files()}
    assert ".env" not in tracked, ".env carries live credentials and must never be committed (D-003)"


def test_no_secret_shaped_string_in_any_tracked_file():
    offenders: list[str] = []
    for path in _tracked_files():
        if not path.is_file():
            continue
        try:
            content = path.read_bytes()
        except OSError:
            continue
        for label, pattern in SECRET_PATTERNS.items():
            match = pattern.search(content)
            if match:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {label}")
    assert not offenders, f"secret-shaped strings committed:\n" + "\n".join(offenders)
