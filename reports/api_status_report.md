# WafGuard API endpoint status report

- Probed: 2026-10-15 13:18 -> 13:26 UTC against a live `uvicorn app.main:app --reload --port 8000` (API v1, MongoDB Atlas dev cluster, 7/7 models warm).
- Auth: `admin` token for everything except the deliberate 401/403 probes (`viewer` / no token).
- Verdict rule: **PASS** = actual HTTP status matched the expectation for that probe (a 401 on an intentionally unauthenticated probe is a pass; a 500 is never a pass).
- Resources used: lot `LOT_14963`, equipment `CMP_001`, wafer `LOT_14963_W01`, alert `6ac7f345db534ecdfd89943d`, job `4d0bf7acf4c84dc39b5c322140216c5f`.
- Result: **92 pass / 4 fail** out of 96 probes.

## NOT WORKING

| Method | Path | Expected | Got | Error code | Note |
|---|---|---|---|---|---|
| GET | `/api/v1/lots?limit=5` | [200] | 500 | INTERNAL_ERROR | An unexpected error occurred. Check server logs. |
| GET | `/api/v1/equipment/CMP_001` | [200] | 500 | INTERNAL_ERROR | An unexpected error occurred. Check server logs. |
| POST | `/api/v1/data/import [empty import]` | [200] | 500 | INTERNAL_ERROR | empty import | An unexpected error occurred. Check server logs. |
| GET | `/api/equipment/CMP_001 [alias /api]` | [200] | 500 | INTERNAL_ERROR | alias /api | An unexpected error occurred. Check server logs. |

## Root cause of the 4 failures

Two bugs account for all four rows (the alias row is the same bug as the `/api/v1` one).

**Bug 1 — `NaN` floats stored in lot documents break JSON serialization (3 of 4).**
`GET /api/lots` and `GET /api/equipment/{id}` both 500 while FastAPI serializes the payload:
`ValueError: Out of range float values are not JSON compliant: nan`. 136 of the first 300 lot
documents in the dev cluster (45%) carry a `NaN` inside `spatial_signature`; every route that
returns those documents raw dies — `/api/equipment/{id}` fails through its embedded `recent_lots`.
`NaN` is not legal JSON, so it must never reach a response body: sanitize at write time
(store `null` instead of `NaN`) in the seeder/import path.

**Bug 2 — `POST /data/import` with an empty `lots` list (1 of 4).**
`validate_tables()` indexes `lots["lot_id"]` on a DataFrame built from `[]`, raising
`KeyError: 'lot_id'` (`backend/app/quality/validation.py:101`). An empty import should be a 422
validation error or `imported: 0`, not a 500.

## Performance notes (slowest endpoints, one run)

| ms | Method | Path |
|---|---|---|
| 84338 | GET | `/api/v1/lot-risk/upcoming` |
| 42682 | POST | `/api/v1/quality/monitoring/snapshots` |
| 31737 | POST | `/api/v1/lots/{lot}/copilot` |
| 24576 | POST | `/api/v1/alerts/scan` |
| 19465 | GET | `/api/v1/equipment/{id}/control-chart?parameter=rf_power` |
| 17183 | GET | `/api/v1/similar/{lot}` |
| 16484 | GET | `/api/v1/lots/{lot}/recommendations` |
| 13215 | GET | `/api/v1/dashboard` |

Several reads exceed 10 s and `lot-risk/upcoming` exceeded 60 s — fine for a demo, but any
browser-facing page that chains them will feel broken.

## Notes on how this was probed

- Every documented operation from `/openapi.json` (58) exercised on `/api/v1`, plus a 15-route
  parity sample on the `/api` alias; `/` → 307 → `/docs` and the Swagger UI renders.
- Writes were kept safe for a shared dev cluster: channel create + delete cancels out, alert scan
  dedupes per lot, data import sent an empty list, and acknowledge / resolve / redeliver were
  probed with a **nonexistent** alert id (404) so no real alert state was changed.
- Auth gates verified: no token → `401 UNAUTHORIZED`; `viewer` on admin-only routes → `403 FORBIDDEN`.
- Payload-shape gotcha (not a bug): `POST /api/v1/whatif` takes `changes`, while
  `POST /api/v1/what-if` takes `overrides` — both reach the counterfactual engine.
- Reproduce: `cd backend && ./.venv/Scripts/python.exe _probe_api.py` (regenerates this file).

## WORKING

| Method | Path | Status | Error code | ms | Note |
|---|---|---|---|---|---|
| POST | `/api/auth/login [admin]` | 200 | - | 4947.2 | admin |
| POST | `/api/auth/login [bad password]` | 401 | UNAUTHORIZED | 941.4 | bad password | Invalid username or password. |
| POST | `/api/auth/login [viewer]` | 200 | - | 865.6 | viewer |
| GET | `/` | 307 | - | 10.1 | Location: /docs |
| GET | `/docs` | 200 | - | 10.0 | - |
| GET | `/api/health` | 200 | - | 83.7 | - |
| GET | `/api/health/live` | 200 | - | 7.6 | - |
| GET | `/api/health/ready` | 200 | - | 67.1 | - |
| GET | `/api/v1/lots [no token]` | 401 | UNAUTHORIZED | 66.2 | no token | Missing bearer token. |
| GET | `/api/v1/dashboard [no token]` | 401 | UNAUTHORIZED | 14.5 | no token | Missing bearer token. |
| GET | `/api/v1/equipment` | 200 | - | 455.9 | - |
| GET | `/api/v1/alerts?limit=5` | 200 | - | 813.1 | - |
| GET | `/api/v1/wafers?lot_id=LOT_14963` | 200 | - | 204.4 | - |
| GET | `/api/v1/dashboard` | 200 | - | 13215.2 | - |
| GET | `/api/v1/lots/LOT_14963` | 200 | - | 2229.7 | - |
| GET | `/api/v1/lots/LOT_DOES_NOT_EXIST` | 404 | NOT_FOUND | 205.2 | Lot LOT_DOES_NOT_EXIST was not found. |
| GET | `/api/v1/lots/LOT_14963/timeline` | 200 | - | 383.0 | - |
| GET | `/api/v1/lots/LOT_14963/root-cause` | 200 | - | 236.8 | - |
| GET | `/api/v1/lots/LOT_14963/recommendations` | 200 | - | 16484.5 | - |
| GET | `/api/v1/lots/LOT_14963/evidence-graph?top_n=3` | 200 | - | 2054.0 | - |
| GET | `/api/v1/recommendations/LOT_14963` | 200 | - | 706.3 | - |
| GET | `/api/v1/root-cause/LOT_14963` | 200 | - | 315.5 | - |
| GET | `/api/v1/root-cause/LOT_14963/graph` | 200 | - | 2527.9 | - |
| GET | `/api/v1/similar/LOT_14963` | 200 | - | 17182.8 | - |
| GET | `/api/v1/similarity/LOT_14963` | 200 | - | 17063.8 | - |
| GET | `/api/v1/defects/lots/LOT_14963` | 200 | - | 299.7 | - |
| GET | `/api/v1/quality/lots/LOT_14963` | 200 | - | 158.7 | - |
| GET | `/api/v1/quality/summary` | 200 | - | 235.6 | - |
| GET | `/api/v1/quality/drift?window_days=30` | 200 | - | 11376.8 | - |
| GET | `/api/v1/quality/monitoring?limit=10` | 200 | - | 176.0 | - |
| GET | `/api/v1/equipment` | 200 | - | 143.1 | - |
| GET | `/api/v1/equipment/NO_SUCH_TOOL` | 404 | NOT_FOUND | 2383.9 | Equipment NO_SUCH_TOOL was not found. |
| GET | `/api/v1/equipment/CMP_001/control-chart?parameter=rf_power` | 200 | - | 19465.3 | - |
| GET | `/api/v1/equipment/CMP_001/control-chart?parameter=vibration` | 404 | NOT_FOUND | 140.4 | Unknown control-chart parameter 'vibration'. Valid: temperature, pressure, rf_power, gas_flow. |
| GET | `/api/v1/equipment/CMP_001/timeline` | 200 | - | 463.2 | - |
| GET | `/api/v1/wafers?lot_id=LOT_14963` | 200 | - | 179.4 | - |
| GET | `/api/v1/wafers/LOT_14963_W01/map` | 200 | - | 152.5 | - |
| GET | `/api/v1/wafers/NO_SUCH_TOOL/map` | 404 | NOT_FOUND | 149.1 | Wafer NO_SUCH_TOOL was not found. |
| GET | `/api/v1/risk/lots` | 200 | - | 186.1 | - |
| GET | `/api/v1/lot-risk/upcoming` | 200 | - | 84337.6 | - |
| GET | `/api/v1/model-performance` | 200 | - | 749.6 | - |
| GET | `/api/v1/models` | 200 | - | 765.2 | - |
| GET | `/api/v1/whatif/limits` | 200 | - | 27.4 | - |
| GET | `/api/v1/alerts?limit=10` | 200 | - | 152.1 | - |
| GET | `/api/v1/alerts/channels` | 200 | - | 146.7 | - |
| GET | `/api/v1/alerts/deliveries?limit=5` | 200 | - | 163.1 | - |
| GET | `/api/v1/alerts/6ac7f345db534ecdfd89943d/timeline` | 200 | - | 290.0 | - |
| GET | `/api/v1/alerts/000000000000000000000000/timeline` | 404 | NOT_FOUND | 223.3 | Alert 000000000000000000000000 was not found. |
| GET | `/api/v1/jobs?limit=10` | 200 | - | 154.3 | - |
| GET | `/api/v1/ops/health-probe` | 200 | - | 12.1 | - |
| GET | `/api/v1/ops/metrics` | 200 | - | 11.2 | - |
| POST | `/api/v1/lots/LOT_14963/copilot` | 200 | - | 31736.7 | - |
| POST | `/api/v1/copilot/ask [global]` | 200 | - | 80.8 | global |
| POST | `/api/v1/predict` | 200 | - | 570.7 | - |
| POST | `/api/v1/yield/predict` | 200 | - | 581.5 | - |
| POST | `/api/v1/whatif` | 200 | - | 1354.1 | - |
| POST | `/api/v1/what-if [alias route (overrides)]` | 200 | - | 475.6 | alias route (overrides) |
| POST | `/api/v1/whatif [unknown lot]` | 404 | NOT_FOUND | 264.0 | unknown lot | Lot LOT_DOES_NOT_EXIST was not found. |
| POST | `/api/v1/whatif [blocked change]` | 422 | VALIDATION_ERROR | 109.6 | blocked change | Change blocked: temperature=900.0 degC is outside the physical limit [415.0, 460.0] |
| POST | `/api/v1/lot-risk` | 200 | - | 15889.4 | - |
| POST | `/api/v1/quality/monitoring/snapshots [admin rebuild]` | 200 | - | 42682.0 | admin rebuild |
| POST | `/api/v1/quality/monitoring/snapshots [viewer denied]` | 403 | FORBIDDEN | 97.3 | viewer denied | Role 'viewer' is not permitted to perform this action. |
| POST | `/api/v1/alerts/scan [admin scan]` | 200 | - | 24576.5 | admin scan |
| POST | `/api/v1/alerts/channels [create probe channel]` | 201 | - | 571.7 | create probe channel |
| DELETE | `/api/v1/alerts/channels/probe-log-tmp [delete probe channel]` | 200 | - | 347.0 | delete probe channel |
| DELETE | `/api/v1/alerts/channels/log [built-in protected (422 if seeded)]` | 404 | NOT_FOUND | 309.2 | built-in protected (422 if seeded) | Channel 'log' was not found. |
| DELETE | `/api/v1/alerts/channels/probe-log-tmp [already gone]` | 404 | NOT_FOUND | 203.9 | already gone | Channel 'probe-log-tmp' was not found. |
| POST | `/api/v1/alerts/000000000000000000000000/acknowledge [nonexistent alert]` | 404 | NOT_FOUND | 305.7 | nonexistent alert | Alert 000000000000000000000000 was not found. |
| POST | `/api/v1/alerts/000000000000000000000000/resolve [nonexistent alert]` | 404 | NOT_FOUND | 413.0 | nonexistent alert | Alert 000000000000000000000000 was not found. |
| POST | `/api/v1/alerts/000000000000000000000000/redeliver [nonexistent alert]` | 404 | NOT_FOUND | 303.8 | nonexistent alert | Alert 000000000000000000000000 was not found. |
| POST | `/api/v1/defects/analyze [non-image file]` | 415 | UNSUPPORTED_FILE | 2224.7 | non-image file | Could not read 'defects.csv' as an image. |
| POST | `/api/v1/defects/analyze [synthetic wafer-map png]` | 200 | - | 314.3 | synthetic wafer-map png |
| POST | `/api/v1/jobs` | 202 | - | 208.4 | - |
| POST | `/api/v1/jobs [unknown job type]` | 422 | VALIDATION_ERROR | 880.2 | unknown job type | unknown job type 'not_a_task'; available: ['data_quality_batch', 'root_cause_batch', 'upcoming_lot_r |
| POST | `/api/v1/jobs [viewer denied]` | 403 | FORBIDDEN | 146.4 | viewer denied | Role 'viewer' is not permitted to perform this action. |
| GET | `/api/v1/jobs/4d0bf7acf4c84dc39b5c322140216c5f` | 200 | - | 198.8 | - |
| GET | `/api/v1/jobs/job_nope` | 404 | NOT_FOUND | 205.0 | Job job_nope was not found. |
| GET | `/api/v1/jobs/4d0bf7acf4c84dc39b5c322140216c5f/result [2xx once done / 409 while running]` | 200 | - | 292.7 | 2xx once done / 409 while running |
| GET | `/api/health [alias /api]` | 200 | - | 108.9 | alias /api |
| GET | `/api/dashboard [alias /api]` | 200 | - | 8811.0 | alias /api |
| GET | `/api/lots/LOT_14963 [alias /api]` | 200 | - | 196.9 | alias /api |
| GET | `/api/equipment [alias /api]` | 200 | - | 204.9 | alias /api |
| GET | `/api/wafers?lot_id=LOT_14963 [alias /api]` | 200 | - | 2216.1 | alias /api |
| GET | `/api/root-cause/LOT_14963 [alias /api]` | 200 | - | 239.3 | alias /api |
| GET | `/api/similarity/LOT_14963 [alias /api]` | 200 | - | 15995.8 | alias /api |
| GET | `/api/quality/summary [alias /api]` | 200 | - | 535.5 | alias /api |
| GET | `/api/whatif/limits [alias /api]` | 200 | - | 7.5 | alias /api |
| GET | `/api/alerts?limit=5 [alias /api]` | 200 | - | 244.7 | alias /api |
| GET | `/api/models [alias /api]` | 200 | - | 1535.9 | alias /api |
| GET | `/api/risk/lots [alias /api]` | 200 | - | 213.8 | alias /api |
| GET | `/api/lot-risk/upcoming [alias /api]` | 200 | - | 61999.0 | alias /api |
| GET | `/api/jobs?limit=5 [alias /api]` | 200 | - | 180.5 | alias /api |
