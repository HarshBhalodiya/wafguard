"""
Runnable script (brief PHASE 3C): build the root-cause evidence graph for a
lot and save it as JSON.

Read-only. Uses the same `app.rootcause.evidence_graph` builder the API uses,
so the script and the endpoint can never drift apart.

Usage:
    cd backend
    .venv\\Scripts\\python.exe scripts/explain_evidence_graph.py --lot-id LOT_10901
    .venv\\Scripts\\python.exe scripts/explain_evidence_graph.py --lot-id LOT_10901 --top-n 5 --out reports/graph.json
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database.mongodb import get_database  # noqa: E402
from app.rootcause import evidence_graph  # noqa: E402
from app.utils.exceptions import NotFoundError  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Windows consoles default to cp1252, but the graph's own text contains
# characters like "→" and "×". Reconfigure the streams rather than degrade the
# output text to plain ASCII.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover - stream does not support it
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the root-cause evidence graph for one lot.")
    parser.add_argument("--lot-id", required=True, help="Lot to explain, e.g. LOT_10901")
    parser.add_argument("--top-n", type=int, default=3, help="How many ranked candidates to include (1-10)")
    parser.add_argument("--out", default=None, help="Output JSON path (default reports/evidence_graph_<lot>.json)")
    args = parser.parse_args()

    if not 1 <= args.top_n <= 10:
        print("--top-n must be between 1 and 10", file=sys.stderr)
        return 2

    try:
        graph = evidence_graph.build_evidence_graph(get_database(), args.lot_id, top_n=args.top_n)
    except NotFoundError as exc:
        print(f"Cannot build a graph: {exc}", file=sys.stderr)
        print(
            "A lot needs a backfilled prediction first — run scripts/train_yield_model.py "
            "(it backfills predictions) or POST /api/yield/predict for this lot.",
            file=sys.stderr,
        )
        return 1

    out_path = Path(args.out) if args.out else PROJECT_ROOT / "reports" / f"evidence_graph_{args.lot_id}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(graph, indent=2, default=str), encoding="utf-8")

    print(f"Evidence graph for {graph['lot_id']} (rules v{graph['rules_version']})")
    print(f"  dominant wafer pattern : {graph['dominant_wafer_pattern']}")
    print(f"  nodes                  : {len(graph['nodes'])}")
    print(f"  edges                  : {len(graph['edges'])}")
    print("\n  Nodes:")
    for node in graph["nodes"]:
        value = "" if node["value"] is None else f" = {node['value']}"
        print(f"    [{node['source']:8s}] {node['type']:14s} {node['label']}{value}")
    print("\n  Edges:")
    for edge in graph["edges"]:
        print(f"    {edge['source']} -> {edge['target']}  strength={edge['strength']} ({edge['kind']})")
    if graph["gaps"]:
        print("\n  Gaps (no invented links):")
        for gap in graph["gaps"]:
            print(f"    - {gap}")
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
