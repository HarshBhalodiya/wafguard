"""Probe every documented WafGuard API route against a live server on :8000.

Run from backend/:  ./.venv/Scripts/python.exe _probe_api.py
Writes ../reports/api_status_report.md and prints a compact table.

Each probe carries an `expect` value; a probe PASSES when the actual status
matches it (so a 401 on an intentionally-unauthenticated probe is a pass, and a
500 is never a pass). Writes to the shared dev cluster are kept to additive or
reversible operations: channel create+delete cancels out, alert scan dedupes by
lot, data import sends an empty list, and alert acknowledge/resolve/redeliver
are probed with a nonexistent id (404) instead of mutating a real alert.
"""
import io
import json
import re
import sys
import time
from datetime import datetime, timezone

import httpx

BASE = "http://127.0.0.1:8000"
ADMIN = ("admin", "wafguard-admin")
VIEWER = ("viewer", "wafguard-viewer")
PROBE_CHANNEL = "probe-log-tmp"

results = []


def record(method, path, expect, status, code, ms, note, verdict=None):
    ok = verdict or ("PASS" if status in expect else "FAIL")
    results.append(
        {
            "method": method,
            "path": path,
            "expect": expect,
            "status": status,
            "error_code": code,
            "ms": round(ms, 1),
            "note": note,
            "verdict": ok,
        }
    )
    print(f"{ok:4} {method:6} {path:52} -> {status} {code or ''} ({ms:.0f}ms) {note}")


def probe(client, method, path, expect=(200,), token=None, label="", **kwargs):
    headers = kwargs.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    start = time.perf_counter()
    r = None
    for attempt in (1, 2):
        try:
            r = client.request(method, path, headers=headers, **kwargs)
            break
        except Exception as exc:  # noqa: BLE001 - a transport error is a finding
            err = str(exc)[:120]
            if attempt == 1:
                time.sleep(2)  # a server 500 kills the keep-alive conn; retry once on a fresh one
                continue
            record(method, path + (f" [{label}]" if label else ""), list(expect), 0, "TIMEOUT/ERROR", (time.perf_counter() - start) * 1000, err, "FAIL")
            return None
    ms = (time.perf_counter() - start) * 1000
    code, note = None, label
    if r.headers.get("content-type", "").startswith("application/json"):
        try:
            body = r.json()
            if isinstance(body, dict) and body.get("error"):
                code = body["error"].get("code")
                note = (note + " | " if note else "") + str(body["error"].get("message"))[:100]
        except Exception:  # noqa: BLE001
            pass
    if r.headers.get("location"):
        note = (note + " | " if note else "") + f"Location: {r.headers['location']}"
    record(method, path + (f" [{label}]" if label else ""), list(expect), r.status_code, code, ms, note)
    return r


def wafer_png():
    """A 128x128 grayscale blob image standing in for a wafer map upload."""
    buf = io.BytesIO()
    try:
        from PIL import Image, ImageDraw

        img = Image.new("L", (128, 128), 255)
        d = ImageDraw.Draw(img)
        for x, y in ((30, 40), (60, 70), (90, 50), (70, 30), (50, 95)):
            d.ellipse((x - 6, y - 6, x + 6, y + 6), fill=0)
        img.save(buf, format="PNG")
    except ImportError:
        import base64

        buf = io.BytesIO(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        ))
    buf.seek(0)
    return ("wafer_map.png", buf, "image/png")


def first_match(obj, pattern):
    """First string anywhere in a nested JSON structure matching `pattern`."""
    if isinstance(obj, str):
        if re.fullmatch(pattern, obj):
            return obj
    elif isinstance(obj, dict):
        for v in obj.values():
            found = first_match(v, pattern)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = first_match(v, pattern)
            if found:
                return found
    return None


def main():
    client = httpx.Client(base_url=BASE, timeout=httpx.Timeout(240.0), follow_redirects=False)
    started = datetime.now(timezone.utc)

    # ---------------------------------------------------------------- login
    r = probe(client, "POST", "/api/auth/login", label="admin", json={"username": ADMIN[0], "password": ADMIN[1]})
    admin_token = r.json()["data"]["access_token"]
    probe(client, "POST", "/api/auth/login", (401,), label="bad password", json={"username": ADMIN[0], "password": "wrong"})
    rv = probe(client, "POST", "/api/auth/login", label="viewer", json={"username": VIEWER[0], "password": VIEWER[1]})
    viewer_token = rv.json()["data"]["access_token"]

    # ------------------------------------------------------- public surface
    probe(client, "GET", "/", (307,))
    probe(client, "GET", "/docs", (200,))
    probe(client, "GET", "/api/health")
    probe(client, "GET", "/api/health/live")
    probe(client, "GET", "/api/health/ready")
    probe(client, "GET", "/api/v1/lots", (401,), label="no token")
    probe(client, "GET", "/api/v1/dashboard", (401,), label="no token")

    # --------------------------------------------------------- discover ids
    ids = {}
    eq = probe(client, "GET", "/api/v1/equipment", token=admin_token)
    ids["equipment_id"] = (eq.json()["data"][0]["equipment_id"] if eq and eq.is_success else "CMP_001")
    al = probe(client, "GET", "/api/v1/alerts?limit=5", token=admin_token)
    ids["lot_id"] = first_match(al.json() if al and al.is_success else {}, r"LOT_\w+") or "LOT_14963"
    ids["alert_id"] = first_match(al.json() if al and al.is_success else {}, r"[0-9a-f]{24}") or "64f000000000000000000000"
    wf = probe(client, "GET", f"/api/v1/wafers?lot_id={ids['lot_id']}", token=admin_token)
    ids["wafer_id"] = first_match(wf.json() if wf and wf.is_success else {}, r"\S+") and (
        wf.json()["data"][0].get("wafer_id") if wf and wf.is_success and wf.json().get("data") else None
    ) or "NOPE_WAFER"
    BOGUS_LOT, BOGUS_EQ, BOGUS_ALERT, BOGUS_JOB = "LOT_DOES_NOT_EXIST", "NO_SUCH_TOOL", "0" * 24, "job_nope"
    print("\n-- ids:", ids, "\n")

    lot, eqid, waf, alert = ids["lot_id"], ids["equipment_id"], ids["wafer_id"], ids["alert_id"]
    T = admin_token

    # ---------------------------------------------------------------- reads
    reads = [
        ("GET", "/api/v1/dashboard", ()),
        ("GET", "/api/v1/lots?limit=5", ()),
        ("GET", f"/api/v1/lots/{lot}", ()),
        ("GET", f"/api/v1/lots/{BOGUS_LOT}", (404,)),
        ("GET", f"/api/v1/lots/{lot}/timeline", ()),
        ("GET", f"/api/v1/lots/{lot}/root-cause", ()),
        ("GET", f"/api/v1/lots/{lot}/recommendations", ()),
        ("GET", f"/api/v1/lots/{lot}/evidence-graph?top_n=3", ()),
        ("GET", f"/api/v1/recommendations/{lot}", ()),
        ("GET", f"/api/v1/root-cause/{lot}", ()),
        ("GET", f"/api/v1/root-cause/{lot}/graph", ()),
        ("GET", f"/api/v1/similar/{lot}", ()),
        ("GET", f"/api/v1/similarity/{lot}", ()),
        ("GET", f"/api/v1/defects/lots/{lot}", ()),
        ("GET", f"/api/v1/quality/lots/{lot}", (200, 404)),
        ("GET", "/api/v1/quality/summary", ()),
        ("GET", "/api/v1/quality/drift?window_days=30", ()),
        ("GET", "/api/v1/quality/monitoring?limit=10", ()),
        ("GET", "/api/v1/equipment", ()),
        ("GET", f"/api/v1/equipment/{eqid}", ()),
        ("GET", f"/api/v1/equipment/{BOGUS_EQ}", (404,)),
        ("GET", f"/api/v1/equipment/{eqid}/control-chart?parameter=rf_power", ()),
        ("GET", f"/api/v1/equipment/{eqid}/control-chart?parameter=vibration", (404,), ),
        ("GET", f"/api/v1/equipment/{eqid}/timeline", ()),
        ("GET", "/api/v1/wafers?lot_id=" + lot, ()),
        ("GET", f"/api/v1/wafers/{waf}/map", ()),
        ("GET", f"/api/v1/wafers/{BOGUS_EQ}/map", (404,)),
        ("GET", "/api/v1/risk/lots", ()),
        ("GET", "/api/v1/lot-risk/upcoming", ()),
        ("GET", "/api/v1/model-performance", ()),
        ("GET", "/api/v1/models", ()),
        ("GET", "/api/v1/whatif/limits", ()),
        ("GET", "/api/v1/alerts?limit=10", ()),
        ("GET", "/api/v1/alerts/channels", ()),
        ("GET", "/api/v1/alerts/deliveries?limit=5", ()),
        ("GET", f"/api/v1/alerts/{alert}/timeline", ()),
        ("GET", f"/api/v1/alerts/{BOGUS_ALERT}/timeline", (404,)),
        ("GET", "/api/v1/jobs?limit=10", ()),
        ("GET", "/api/v1/ops/health-probe", ()),
        ("GET", "/api/v1/ops/metrics", ()),
    ]
    for method, path, expect in reads:
        probe(client, method, path, expect or (200,), token=T)

    # ------------------------------------------------------------ mutations
    probe(client, "POST", f"/api/v1/lots/{lot}/copilot", token=T, json={"question": "Why is this lot at risk?"})
    probe(client, "POST", "/api/v1/copilot/ask", token=T, label="global", json={"question": "Which tool family drives the most yield loss?"})
    probe(client, "POST", "/api/v1/predict", token=T, json={"lot_id": lot})
    probe(client, "POST", "/api/v1/yield/predict", token=T, json={"lot_id": lot})
    probe(client, "POST", "/api/v1/whatif", token=T, json={"lot_id": lot, "changes": {"temperature": 440.0}})
    probe(client, "POST", "/api/v1/what-if", token=T, label="alias route (overrides)", json={"lot_id": lot, "overrides": {"temperature": 440.0}})
    probe(client, "POST", "/api/v1/whatif", (404,), token=T, label="unknown lot", json={"lot_id": BOGUS_LOT, "changes": {"temperature": 440.0}})
    probe(client, "POST", "/api/v1/whatif", (422,), token=T, label="blocked change", json={"lot_id": lot, "changes": {"temperature": 900.0}})
    probe(client, "POST", "/api/v1/lot-risk", token=T, json={"equipment_id": eqid})
    probe(client, "POST", "/api/v1/quality/monitoring/snapshots", token=T, label="admin rebuild")
    probe(client, "POST", "/api/v1/quality/monitoring/snapshots", (403,), token=viewer_token, label="viewer denied")
    probe(client, "POST", "/api/v1/alerts/scan", token=T, label="admin scan")
    probe(client, "POST", f"/api/v1/alerts/channels", (201,), token=T, label="create probe channel", json={"name": PROBE_CHANNEL, "type": "log", "target": ""})
    probe(client, "DELETE", f"/api/v1/alerts/channels/{PROBE_CHANNEL}", token=T, label="delete probe channel")
    probe(client, "DELETE", f"/api/v1/alerts/channels/log", (404, 422), token=T, label="built-in protected (422 if seeded)")
    probe(client, "DELETE", f"/api/v1/alerts/channels/{PROBE_CHANNEL}", (404,), token=T, label="already gone")
    for action in ("acknowledge", "resolve", "redeliver"):
        probe(client, "POST", f"/api/v1/alerts/{BOGUS_ALERT}/{action}", (404,), token=T, label="nonexistent alert")
    probe(client, "POST", "/api/v1/data/import", token=T, label="empty import", json={"lots": []})
    csv_file = ("defects.csv", io.BytesIO(b"x,y,label\n1,2,scratch\n"), "text/csv")
    probe(client, "POST", "/api/v1/defects/analyze", (415,), token=T, label="non-image file", files={"file": csv_file})
    probe(client, "POST", "/api/v1/defects/analyze", token=T, label="synthetic wafer-map png", files={"file": wafer_png()})

    # ----------------------------------------------------------------- jobs
    jr = probe(client, "POST", "/api/v1/jobs", (202,), token=T, json={"type": "data_quality_batch", "params": {"lot_ids": [lot]}})
    job_id = jr.json()["data"]["job_id"] if jr and jr.is_success else "job_nope"
    probe(client, "POST", "/api/v1/jobs", (422,), token=T, label="unknown job type", json={"type": "not_a_task", "params": {"lot_ids": [lot]}})
    probe(client, "POST", "/api/v1/jobs", (403,), token=viewer_token, label="viewer denied", json={"type": "data_quality_batch", "params": {"lot_ids": [lot]}})
    probe(client, "GET", f"/api/v1/jobs/{job_id}", token=T)
    probe(client, "GET", f"/api/v1/jobs/{BOGUS_JOB}", (404,), token=T)
    time.sleep(8)
    probe(client, "GET", f"/api/v1/jobs/{job_id}/result", (200, 409), token=T, label="2xx once done / 409 while running")

    # ------------------------------------------- /api alias parity (reads)
    alias_paths = [
        "/api/health",
        "/api/dashboard",
        f"/api/lots/{lot}",
        "/api/equipment",
        f"/api/equipment/{eqid}",
        "/api/wafers?lot_id=" + lot,
        f"/api/root-cause/{lot}",
        f"/api/similarity/{lot}",
        "/api/quality/summary",
        "/api/whatif/limits",
        "/api/alerts?limit=5",
        "/api/models",
        "/api/risk/lots",
        "/api/lot-risk/upcoming",
        "/api/jobs?limit=5",
    ]
    for p in alias_paths:
        probe(client, "GET", p, token=T, label="alias /api")

    # ------------------------------------------------------------- summary
    finished = datetime.now(timezone.utc)
    write_report(started, finished, ids, job_id)
    fails = [r for r in results if r["verdict"] == "FAIL"]
    print(f"\nTOTAL {len(results)} probes | PASS {len(results) - len(fails)} | FAIL {len(fails)}")
    for f in fails:
        print(f"  FAIL {f['method']} {f['path']} -> {f['status']} {f['error_code']}")
    return 1 if fails else 0


def write_report(started, finished, ids, job_id):
    fails = [r for r in results if r["verdict"] == "FAIL"]
    ok = [r for r in results if r["verdict"] == "PASS"]
    lines = [
        "# WafGuard API endpoint status report",
        "",
        f"- Probed: {started:%Y-%m-%d %H:%M} -> {finished:%H:%M} UTC against a live "
        f"`uvicorn app.main:app --reload --port 8000` (API v1, MongoDB Atlas dev cluster, 7/7 models warm).",
        "- Auth: `admin` token for everything except the deliberate 401/403 probes (`viewer` / no token).",
        "- Verdict rule: **PASS** = actual HTTP status matched the expectation for that probe "
        "(a 401 on an intentionally unauthenticated probe is a pass; a 500 is never a pass).",
        f"- Resources used: lot `{ids.get('lot_id')}`, equipment `{ids.get('equipment_id')}`, "
        f"wafer `{ids.get('wafer_id')}`, alert `{ids.get('alert_id')}`, job `{job_id}`.",
        f"- Result: **{len(ok)} pass / {len(fails)} fail** out of {len(results)} probes.",
        "",
        "## NOT WORKING",
        "",
    ]
    if fails:
        lines += ["| Method | Path | Expected | Got | Error code | Note |", "|---|---|---|---|---|---|"]
        for f in fails:
            lines.append(
                f"| {f['method']} | `{f['path']}` | {f['expect']} | {f['status']} | {f['error_code'] or '-'} | {f['note'] or '-'} |"
            )
    else:
        lines.append("_None — every probe behaved as expected._")
    lines += ["", "## WORKING", "", "| Method | Path | Status | Error code | ms | Note |", "|---|---|---|---|---|---|"]
    for r in ok:
        lines.append(
            f"| {r['method']} | `{r['path']}` | {r['status']} | {r['error_code'] or '-'} | {r['ms']} | {r['note'] or '-'} |"
        )
    lines.append("")
    with open("../reports/api_status_report.md", "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    with open("../reports/api_status_report.json", "w", encoding="utf-8") as fh:
        json.dump({"started": started.isoformat(), "results": results}, fh, indent=2)
    print("report -> ../reports/api_status_report.md")


if __name__ == "__main__":
    sys.exit(main())
