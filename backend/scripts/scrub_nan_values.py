"""
One-off data repair: replace NaN / Infinity doubles with BSON null so the API
can serialize the documents again.

    cd backend
    .venv/Scripts/python.exe scripts/scrub_nan_values.py                # lots collection (default)
    .venv/Scripts/python.exe scripts/scrub_nan_values.py --dry-run      # report only, write nothing
    .venv/Scripts/python.exe scripts/scrub_nan_values.py --collection wafer_maps

Background: pandas turns an empty CSV cell into float('nan'), and the seeder
used to copy that straight into Mongo. NaN is legal IEEE-754 and stores fine,
but it is not legal JSON, so `json.dumps` raised ValueError and every endpoint
returning such a document 500'd (GET /api/lots, GET /api/equipment/{id} via its
embedded recent_lots). The write path is scrubbed now (app.utils.mongo_sanitize
applied in BaseRepository + the seeder), so this script only repairs documents
that predate that fix.

Idempotent: a second run finds nothing to change and reports 0 modified.
Only `$set`s the offending dotted paths, so no other field is touched.
"""
import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo import UpdateOne  # noqa: E402

from app.database import mongodb  # noqa: E402
from app.utils.logging import get_logger  # noqa: E402

logger = get_logger(__name__)

# The collections the API serves raw documents from. lots is the one proven to
# be affected; the rest are scanned on request via --collection / --all.
DEFAULT_COLLECTION = "lots"

# The cluster is remote: one update_one per document would be thousands of
# round trips. Updates are batched through bulk_write instead (still per-document
# $set of only the offending paths), and progress is printed so a long run is
# observable rather than silent.
WRITE_BATCH = 200
PROGRESS_EVERY = 1000


def non_finite_paths(value, prefix: str = "") -> list[str]:
    """Dotted paths of every non-finite float (or NaT) nested in `value`."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            found += non_finite_paths(item, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found += non_finite_paths(item, f"{prefix}.{index}")
    elif isinstance(value, float) and not math.isfinite(value):
        found.append(prefix)
    elif type(value).__name__ == "NaTType":
        found.append(prefix)
    return found


def scrub_collection(db, name: str, dry_run: bool) -> tuple[int, dict]:
    """Rewrite non-finite doubles in one collection as null. Returns (docs_modified, {path: count})."""
    collection = db[name]
    modified = 0
    scanned = 0
    field_counts: dict[str, int] = {}
    pending: list = []

    def flush() -> None:
        if pending and not dry_run:
            collection.bulk_write(pending, ordered=False)
        pending.clear()

    for doc in collection.find({}):
        scanned += 1
        paths = non_finite_paths({k: v for k, v in doc.items() if k != "_id"})
        if paths:
            for path in paths:
                field_counts[path] = field_counts.get(path, 0) + 1
            update = {path: None for path in paths}  # paths are unique, so no key collision
            pending.append(UpdateOne({"_id": doc["_id"]}, {"$set": update}))
            modified += 1
            if len(pending) >= WRITE_BATCH:
                flush()
                print(f"[scrub]   ...{scanned} scanned, {modified} modified", flush=True)
        elif PROGRESS_EVERY and scanned % PROGRESS_EVERY == 0:
            print(f"[scrub]   ...{scanned} scanned, {modified} modified", flush=True)
    flush()
    print(f"[scrub]   scanned {scanned} document(s)", flush=True)
    return modified, field_counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Replace NaN/Infinity doubles with null in MongoDB documents.")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION, help=f"collection to scrub (default: {DEFAULT_COLLECTION})")
    parser.add_argument("--dry-run", action="store_true", help="report what would change; write nothing")
    args = parser.parse_args()

    # Remote cluster: allow a patient handshake rather than the API's 1.5s fail-fast.
    mongodb.connect(timeout_ms=10000)
    if not mongodb.is_connected():
        print("MongoDB is not reachable - nothing was changed.", file=sys.stderr)
        return 1
    db = mongodb.get_database()

    modified, field_counts = scrub_collection(db, args.collection, args.dry_run)
    verb = "would modify" if args.dry_run else "modified"
    print(f"[scrub] {args.collection}: {verb} {modified} document(s)")
    for path, count in sorted(field_counts.items(), key=lambda kv: -kv[1]):
        print(f"[scrub]   {path}: {count} value(s)")
    if not field_counts:
        print("[scrub]   nothing to do - collection is already clean")
    mongodb.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
