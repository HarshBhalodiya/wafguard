# Setup Guide

This file is read by the automated evaluation pipeline. Be precise and complete.

---

## Prerequisites

Before you begin, ensure you have the following installed:

- **Python 3.11+** (developed & tested on Python 3.13)
- **Node.js 18+** and **npm**
- **MongoDB Community Server 6.0+** (running locally on `mongodb://localhost:27017`)
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
| `SECOM_DATA_PATH` | Storage location for SECOM dataset files (`./data/secom`) | Yes |
| `WM811K_DATA_PATH` | Storage location for WM-811K wafer map dataset (`./data/wm811k`) | No |
| `SYNTHETIC_DATA_PATH` | Storage location for generated manufacturing data (`./data/synthetic`) | Yes |
| `MODEL_DIR` | Storage directory for trained ML artifacts (`./backend/models/artifacts`) | Yes |
| `CORS_ORIGINS` | Allowed origins for cross-origin API requests | Yes |
| `JWT_SECRET_KEY` | Secret key used for signing authentication JWT tokens | Yes |
| `LLM_API_KEY` | Optional IBM watsonx.ai / Anthropic API key for natural-language Copilot | No |
| `VITE_API_BASE_URL` | Frontend REST API base URL (`http://localhost:8000/api`) | Yes |

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

# 4. Download data, seed database, and train ML models
cd ../backend
python scripts/download_secom.py          # 1. Fetch real SECOM dataset from UCI
python scripts/generate_synthetic_data.py  # 2. Generate manufacturing telemetry layer
python scripts/seed_database.py            # 3. Load seed entities into MongoDB
python scripts/train_yield_model.py        # 4. Train XGBoost tabular models & backfill predictions
python scripts/train_wafer_model.py        # 5. Train PyTorch CNN wafer defect classifier
```

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

---

## Running Tests

```bash
# Run backend test suite (39 tests covering services & API endpoints via mongomock)
cd backend
.venv\Scripts\activate
pytest

# Run frontend test suite (17 component & page tests via Vitest)
cd frontend
npm test
```
