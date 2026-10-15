# Setup Guide

This file is read by the automated evaluation pipeline. Be precise and complete.

---

## Prerequisites

Before you begin, ensure you have the following installed:

- **Python 3.11+** (developed & tested on Python 3.14)
- **Node.js 18+** and **npm**
- **MongoDB 6.0+** (running locally on `mongodb://localhost:27017`, or any Atlas cluster)
- **Git**

---

## Environment Variables

Copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
```

| Variable | Description | Required |
|---|---|---|
| `MONGODB_URI` | MongoDB connection string (e.g., `mongodb://localhost:27017`) | Yes |
| `MONGODB_DATABASE` | Database name (default: `wafguard`) | Yes |
| `SECOM_DATA_PATH` | Storage location for SECOM dataset files (`./data/secom`) | No (optional real dataset; fetched by `scripts/download_secom.py`) |
| `WM811K_DATA_PATH` | Storage location for WM-811K wafer map dataset (`./data/wm811k`) | No (optional real dataset) |
| `SYNTHETIC_DATA_PATH` | Storage location for generated manufacturing data (`./data/synthetic`) | Yes |
| `MODEL_DIR` | Storage directory for trained ML artifacts (`./backend/models/artifacts`) | Yes |
| `CORS_ORIGINS` | Allowed origins for cross-origin API requests | Yes |
| `JWT_SECRET_KEY` | Secret key used for signing authentication JWT tokens | Yes |
| `LLM_API_KEY` | Optional Anthropic / OpenAI-compatible API key for the Copilot phrasing layer (leave blank for the deterministic fallback) | No |
| `VITE_API_BASE_URL` | Frontend REST API base URL (`/api` via Vite/nginx proxy) | Yes |

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/wafguard/wafguard-ai.git
cd wafguard-ai

# 2. Install backend dependencies
cd backend
python -m venv .venv
.venv\Scripts\activate   # On Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt

# 3. Install frontend dependencies
cd ../frontend
npm install
```

---

## Building the Data & Models (one command)

From `backend/`, with the virtualenv activated and `.env` configured:

```bash
# One command: generate the synthetic dataset -> seed MongoDB ->
# train yield models (+ backfill predictions/alerts) -> write evaluation_report.json
python scripts/build_dataset.py --all
```

The same chain is resumable and composable:

```bash
python scripts/build_dataset.py                  # generate only (default)
python scripts/build_dataset.py --from seed      # resume at seeding
python scripts/build_dataset.py --stages train,evaluate
python scripts/build_dataset.py --plan --all     # print the resolved plan, run nothing
```

Additional (optional) model stages, run individually:

```bash
python scripts/train_wafer_model.py        # PyTorch CNN wafer defect classifier (+ embeddings)
python scripts/train_temporal_model.py     # GRU/Transformer temporal models (+ embeddings)
python scripts/build_evidence_tables.py    # measured pattern->cause evidence table
```

Real public datasets (optional): `python scripts/download_secom.py` (UCI SECOM).

**Preflight check:** `python scripts/doctor.py` diagnoses the whole setup (env, MongoDB,
dataset fingerprint, trained artifacts, evidence table, embeddings) and prints the exact
command that fixes anything missing. Exit code 0 = ready.

---

## Running the Application

```bash
# Start the backend API server
cd backend
.venv\Scripts\activate   # On Linux/macOS: source .venv/bin/activate
uvicorn app.main:app --reload

# Start the frontend dev server (in a separate terminal)
cd frontend
npm run dev
```

The application will be available at:
- **Frontend UI Dashboard:** `http://localhost:5173`
- **Interactive Backend API Docs:** `http://localhost:8000/docs`

**Authentication (RBAC):** every API route except `/api/health` and `POST /api/auth/login`
requires a JWT. Log in with a seeded demo account, e.g.
`admin` / `wafguard-admin` (see the README for the full role matrix), then pass
`Authorization: Bearer <token>` on subsequent requests. The frontend login page does this
automatically.

`GET /api/health` needs no token and reports database reachability plus a readiness block
(dataset fingerprint, whether the trained tabular models are present).

---

## Running Tests

```bash
# Run backend test suite (pytest; runs against an in-memory mongomock database,
# no MongoDB server or trained artifacts required — the suite trains its own
# small models in a temp dir and never touches backend/models/artifacts/)
cd backend
.venv\Scripts\activate
pytest

# Run frontend test suite (Vitest)
cd frontend
npm test
```
