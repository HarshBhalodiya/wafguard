# WafGuard API endpoint status report

- Probed: 2026-10-16 07:06 -> 07:17 UTC against a live `uvicorn app.main:app --reload --port 8000` (API v1, MongoDB Atlas dev cluster, 7/7 models warm).
- Auth: `admin` token for everything except the deliberate 401/403 probes (`viewer` / no token).
- Verdict rule: **PASS** = actual HTTP status matched the expectation for that probe (a 401 on an intentionally unauthenticated probe is a pass; a 500 is never a pass).
- Resources used: lot `LOT_14963`, equipment `CMP_001`, wafer `LOT_14963_W01`, alert `6ac7f345db534ecdfd89943d`, job `a1e50531d46246669cf77e8f0f88ab35`.
- Result: **96 pass / 0 fail** out of 96 probes.

## NOT WORKING

_None — every probe behaved as expected._

## WORKING

| Method | Path | Status | Error code | ms | Note |
|---|---|---|---|---|---|
| POST | `/api/auth/login [admin]` | 200 | - | 1372.1 | admin |
| POST | `/api/auth/login [bad password]` | 401 | UNAUTHORIZED | 1110.3 | bad password | Invalid username or password. |
| POST | `/api/auth/login [viewer]` | 200 | - | 643.3 | viewer |
| GET | `/` | 307 | - | 3.8 | Location: /docs |
| GET | `/docs` | 200 | - | 3.2 | - |
| GET | `/api/health` | 200 | - | 178.4 | - |
| GET | `/api/health/live` | 200 | - | 4.0 | - |
| GET | `/api/health/ready` | 200 | - | 108.2 | - |
| GET | `/api/v1/lots [no token]` | 401 | UNAUTHORIZED | 11.7 | no token | Missing bearer token. |
| GET | `/api/v1/dashboard [no token]` | 401 | UNAUTHORIZED | 11.1 | no token | Missing bearer token. |
| GET | `/api/v1/equipment` | 200 | - | 257.7 | - |
| GET | `/api/v1/alerts?limit=5` | 200 | - | 392.7 | - |
| GET | `/api/v1/wafers?lot_id=LOT_14963` | 200 | - | 307.4 | - |
| GET | `/api/v1/dashboard` | 200 | - | 20663.8 | - |
| GET | `/api/v1/lots?limit=5` | 200 | - | 242.0 | - |
| GET | `/api/v1/lots/LOT_14963` | 200 | - | 127.6 | - |
| GET | `/api/v1/lots/LOT_DOES_NOT_EXIST` | 404 | NOT_FOUND | 168.2 | Lot LOT_DOES_NOT_EXIST was not found. |
| GET | `/api/v1/lots/LOT_14963/timeline` | 200 | - | 252.9 | - |
| GET | `/api/v1/lots/LOT_14963/root-cause` | 200 | - | 205.6 | - |
| GET | `/api/v1/lots/LOT_14963/recommendations` | 200 | - | 17546.3 | - |
| GET | `/api/v1/lots/LOT_14963/evidence-graph?top_n=3` | 200 | - | 2447.1 | - |
| GET | `/api/v1/recommendations/LOT_14963` | 200 | - | 767.2 | - |
| GET | `/api/v1/root-cause/LOT_14963` | 200 | - | 228.2 | - |
| GET | `/api/v1/root-cause/LOT_14963/graph` | 200 | - | 2375.3 | - |
| GET | `/api/v1/similar/LOT_14963` | 200 | - | 14919.2 | - |
| GET | `/api/v1/similarity/LOT_14963` | 200 | - | 34472.2 | - |
| GET | `/api/v1/defects/lots/LOT_14963` | 200 | - | 648.5 | - |
| GET | `/api/v1/quality/lots/LOT_14963` | 200 | - | 152.0 | - |
| GET | `/api/v1/quality/summary` | 200 | - | 318.9 | - |
| GET | `/api/v1/quality/drift?window_days=30` | 200 | - | 15393.2 | - |
| GET | `/api/v1/quality/monitoring?limit=10` | 200 | - | 150.0 | - |
| GET | `/api/v1/equipment` | 200 | - | 149.0 | - |
| GET | `/api/v1/equipment/CMP_001` | 200 | - | 644.4 | - |
| GET | `/api/v1/equipment/NO_SUCH_TOOL` | 404 | NOT_FOUND | 186.5 | Equipment NO_SUCH_TOOL was not found. |
| GET | `/api/v1/equipment/CMP_001/control-chart?parameter=rf_power` | 200 | - | 23204.0 | - |
| GET | `/api/v1/equipment/CMP_001/control-chart?parameter=vibration` | 404 | NOT_FOUND | 347.9 | Unknown control-chart parameter 'vibration'. Valid: temperature, pressure, rf_power, gas_flow. |
| GET | `/api/v1/equipment/CMP_001/timeline` | 200 | - | 873.4 | - |
| GET | `/api/v1/wafers?lot_id=LOT_14963` | 200 | - | 188.5 | - |
| GET | `/api/v1/wafers/LOT_14963_W01/map` | 200 | - | 159.5 | - |
| GET | `/api/v1/wafers/NO_SUCH_TOOL/map` | 404 | NOT_FOUND | 258.0 | Wafer NO_SUCH_TOOL was not found. |
| GET | `/api/v1/risk/lots` | 200 | - | 302.2 | - |
| GET | `/api/v1/lot-risk/upcoming` | 200 | - | 130885.5 | - |
| GET | `/api/v1/model-performance` | 200 | - | 2444.2 | - |
| GET | `/api/v1/models` | 200 | - | 2029.6 | - |
| GET | `/api/v1/whatif/limits` | 200 | - | 6.5 | - |
| GET | `/api/v1/alerts?limit=10` | 200 | - | 399.3 | - |
| GET | `/api/v1/alerts/channels` | 200 | - | 392.8 | - |
| GET | `/api/v1/alerts/deliveries?limit=5` | 200 | - | 371.0 | - |
| GET | `/api/v1/alerts/6ac7f345db534ecdfd89943d/timeline` | 200 | - | 699.3 | - |
| GET | `/api/v1/alerts/000000000000000000000000/timeline` | 404 | NOT_FOUND | 581.6 | Alert 000000000000000000000000 was not found. |
| GET | `/api/v1/jobs?limit=10` | 200 | - | 392.7 | - |
| GET | `/api/v1/ops/health-probe` | 200 | - | 14.2 | - |
| GET | `/api/v1/ops/metrics` | 200 | - | 5.8 | - |
| POST | `/api/v1/lots/LOT_14963/copilot` | 200 | - | 51554.2 | - |
| POST | `/api/v1/copilot/ask [global]` | 200 | - | 156.0 | global |
| POST | `/api/v1/predict` | 200 | - | 915.5 | - |
| POST | `/api/v1/yield/predict` | 200 | - | 800.7 | - |
| POST | `/api/v1/whatif` | 200 | - | 1326.6 | - |
| POST | `/api/v1/what-if [alias route (overrides)]` | 200 | - | 594.9 | alias route (overrides) |
| POST | `/api/v1/whatif [unknown lot]` | 404 | NOT_FOUND | 322.9 | unknown lot | Lot LOT_DOES_NOT_EXIST was not found. |
| POST | `/api/v1/whatif [blocked change]` | 422 | VALIDATION_ERROR | 130.4 | blocked change | Change blocked: temperature=900.0 degC is outside the physical limit [415.0, 460.0] |
| POST | `/api/v1/lot-risk` | 200 | - | 24986.2 | - |
| POST | `/api/v1/quality/monitoring/snapshots [admin rebuild]` | 200 | - | 39643.8 | admin rebuild |
| POST | `/api/v1/quality/monitoring/snapshots [viewer denied]` | 403 | FORBIDDEN | 152.6 | viewer denied | Role 'viewer' is not permitted to perform this action. |
| POST | `/api/v1/alerts/scan [admin scan]` | 200 | - | 24334.3 | admin scan |
| POST | `/api/v1/alerts/channels [create probe channel]` | 201 | - | 443.1 | create probe channel |
| DELETE | `/api/v1/alerts/channels/probe-log-tmp [delete probe channel]` | 200 | - | 295.3 | delete probe channel |
| DELETE | `/api/v1/alerts/channels/log [built-in protected (422 if seeded)]` | 404 | NOT_FOUND | 240.5 | built-in protected (422 if seeded) | Channel 'log' was not found. |
| DELETE | `/api/v1/alerts/channels/probe-log-tmp [already gone]` | 404 | NOT_FOUND | 148.7 | already gone | Channel 'probe-log-tmp' was not found. |
| POST | `/api/v1/alerts/000000000000000000000000/acknowledge [nonexistent alert]` | 404 | NOT_FOUND | 361.7 | nonexistent alert | Alert 000000000000000000000000 was not found. |
| POST | `/api/v1/alerts/000000000000000000000000/resolve [nonexistent alert]` | 404 | NOT_FOUND | 253.4 | nonexistent alert | Alert 000000000000000000000000 was not found. |
| POST | `/api/v1/alerts/000000000000000000000000/redeliver [nonexistent alert]` | 404 | NOT_FOUND | 522.8 | nonexistent alert | Alert 000000000000000000000000 was not found. |
| POST | `/api/v1/data/import [empty import]` | 200 | - | 437.6 | empty import |
| POST | `/api/v1/defects/analyze [non-image file]` | 415 | UNSUPPORTED_FILE | 175.9 | non-image file | Could not read 'defects.csv' as an image. |
| POST | `/api/v1/defects/analyze [synthetic wafer-map png]` | 200 | - | 250.0 | synthetic wafer-map png |
| POST | `/api/v1/jobs` | 202 | - | 325.4 | - |
| POST | `/api/v1/jobs [unknown job type]` | 422 | VALIDATION_ERROR | 887.1 | unknown job type | unknown job type 'not_a_task'; available: ['data_quality_batch', 'root_cause_batch', 'upcoming_lot_r |
| POST | `/api/v1/jobs [viewer denied]` | 403 | FORBIDDEN | 173.5 | viewer denied | Role 'viewer' is not permitted to perform this action. |
| GET | `/api/v1/jobs/a1e50531d46246669cf77e8f0f88ab35` | 200 | - | 226.9 | - |
| GET | `/api/v1/jobs/job_nope` | 404 | NOT_FOUND | 239.6 | Job job_nope was not found. |
| GET | `/api/v1/jobs/a1e50531d46246669cf77e8f0f88ab35/result [2xx once done / 409 while running]` | 200 | - | 420.1 | 2xx once done / 409 while running |
| GET | `/api/health [alias /api]` | 200 | - | 173.2 | alias /api |
| GET | `/api/dashboard [alias /api]` | 200 | - | 24128.5 | alias /api |
| GET | `/api/lots/LOT_14963 [alias /api]` | 200 | - | 298.7 | alias /api |
| GET | `/api/equipment [alias /api]` | 200 | - | 462.5 | alias /api |
| GET | `/api/equipment/CMP_001 [alias /api]` | 200 | - | 941.1 | alias /api |
| GET | `/api/wafers?lot_id=LOT_14963 [alias /api]` | 200 | - | 426.4 | alias /api |
| GET | `/api/root-cause/LOT_14963 [alias /api]` | 200 | - | 423.1 | alias /api |
| GET | `/api/similarity/LOT_14963 [alias /api]` | 200 | - | 60715.1 | alias /api |
| GET | `/api/quality/summary [alias /api]` | 200 | - | 619.3 | alias /api |
| GET | `/api/whatif/limits [alias /api]` | 200 | - | 4.3 | alias /api |
| GET | `/api/alerts?limit=5 [alias /api]` | 200 | - | 414.4 | alias /api |
| GET | `/api/models [alias /api]` | 200 | - | 1866.1 | alias /api |
| GET | `/api/risk/lots [alias /api]` | 200 | - | 597.1 | alias /api |
| GET | `/api/lot-risk/upcoming [alias /api]` | 200 | - | 105292.5 | alias /api |
| GET | `/api/jobs?limit=5 [alias /api]` | 200 | - | 172.8 | alias /api |
