"""
Fetches the real UCI SECOM dataset (public, no login required) into
SECOM_DATA_PATH.

Source: https://archive.ics.uci.edu/dataset/179/secom (mirrored on Kaggle
at https://www.kaggle.com/datasets/paresh2047/uci-semcom). This is a real
semiconductor manufacturing dataset, used here for prototype modeling —
not a specific modern fab.

Usage:
    cd backend
    .venv\\Scripts\\activate
    python scripts/download_secom.py
"""
import io
import sys
import zipfile
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.data.loaders.secom_loader import DATA_FILENAME, LABELS_FILENAME, is_available  # noqa: E402

SECOM_URL = "https://archive.ics.uci.edu/static/public/179/secom.zip"


def main() -> None:
    settings = get_settings()
    target_dir = settings.resolve_path(settings.secom_data_path)
    target_dir.mkdir(parents=True, exist_ok=True)

    if is_available(target_dir):
        print(f"SECOM already present at {target_dir} — nothing to do.")
        return

    print(f"Downloading SECOM from {SECOM_URL} ...")
    response = requests.get(SECOM_URL, timeout=60)
    response.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        print(f"Archive contains: {names}")
        for name in names:
            lower = name.lower()
            if lower.endswith(DATA_FILENAME) or lower.endswith(LABELS_FILENAME):
                dest_name = DATA_FILENAME if lower.endswith(DATA_FILENAME) else LABELS_FILENAME
                with archive.open(name) as src, open(target_dir / dest_name, "wb") as dst:
                    dst.write(src.read())
                print(f"  extracted {name} -> {target_dir / dest_name}")

    if not is_available(target_dir):
        raise SystemExit(
            "Download completed but expected files were not found in the archive. "
            "Check the printed archive contents above and adjust download_secom.py if "
            "UCI has changed the file layout."
        )

    print(f"SECOM dataset ready at {target_dir}")


if __name__ == "__main__":
    main()
