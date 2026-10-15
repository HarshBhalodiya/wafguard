"""
Runnable script (brief PHASE 4): pre-run risk for lots that have not run yet.

Read-only. Uses the same `app.risk.upcoming_lot_risk` engine the API uses, so the
script and the endpoints cannot drift apart.

Usage:
    cd backend
    .venv\\Scripts\\python.exe scripts/score_upcoming_lots.py                 # risk-sorted queue, top 10
    .venv\\Scripts\\python.exe scripts/score_upcoming_lots.py --top 5 --out reports/upcoming_risk.json
    .venv\\Scripts\\python.exe scripts/score_upcoming_lots.py --equipment ETCH_007
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database.mongodb import get_database  # noqa: E402
from app.risk import upcoming_lot_risk  # noqa: E402
from app.utils.exceptions import NotFoundError, ValidationFailedError  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover - stream does not support it
    pass


def _print_entry(entry: dict) -> None:
    print(
        f"  {entry['risk_level']:5s} p_low_yield={entry['risk_probability']:.4f} "
        f"pred_yield={entry['predicted_yield']:5.2f}%  {entry['equipment_id']:12s} "
        f"({entry['process_step']}, product={entry['product']})"
    )
    for factor in entry["top_factors"]:
        value = "n/a" if factor.get("value") is None else factor["value"]
        print(f"        factor: {factor['factor']} = {value} [{factor['source']}]")
    print(
        f"        last known state from {entry['sources']['state_source_lot_id']} "
        f"({entry['sources']['state_source_process_start']}); "
        f"{entry['sources']['n_prior_lots_on_tool']} prior runs on the tool"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Score runs that have not happened yet.")
    parser.add_argument("--equipment", default=None, help="Score only this tool (default: the whole derived queue)")
    parser.add_argument("--top", type=int, default=10, help="How many queue entries to print (1-200)")
    parser.add_argument("--out", default=None, help="Write the JSON payload here")
    args = parser.parse_args()

    if not 1 <= args.top <= 200:
        print("--top must be between 1 and 200", file=sys.stderr)
        return 2

    db = get_database()
    try:
        if args.equipment:
            payload = upcoming_lot_risk.score_planned_run(
                db,
                equipment_id=args.equipment,
                planned_start=upcoming_lot_risk.dataset_now(db),
            )
            print(f"Pre-run estimate for {args.equipment} at {payload['planned_start']}")
            print(f"  {payload['risk_level']} p_low_yield={payload['risk_probability']} "
                  f"pred_yield={payload['predicted_yield']}%")
            for factor in payload["top_factors"]:
                print(f"    factor: {factor['factor']} = {factor.get('value')} [{factor['source']}]")
            payload = {"single": payload}
        else:
            payload = upcoming_lot_risk.upcoming_lot_risks(db, limit=args.top)
            print(f"Derived pre-run queue as of {payload['planned_start']} "
                  f"({payload['n_tools_scored']} tools scored, {payload['n_tools_skipped']} skipped)")
            print(f"  {payload['queue_note']}")
            print(f"  {payload['label']}\n")
            for entry in payload["lots"]:
                _print_entry(entry)
            if payload["skipped"]:
                print("\n  Skipped tools (no prior run — not faked):")
                for skipped in payload["skipped"][:5]:
                    print(f"    - {skipped['equipment_id']}")
    except (NotFoundError, ValidationFailedError) as exc:
        print(f"Cannot score: {exc}", file=sys.stderr)
        return 1

    if args.out:
        out_path = Path(args.out)
        if not out_path.is_absolute():
            out_path = PROJECT_ROOT / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
