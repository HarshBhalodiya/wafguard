# 🚀 WafGuard AI — Yield Prediction & Root-Cause Analysis Platform

---

## 👥 Team

| Field | Value |
|---|---|
| **Team Name** | WafGuard AI squad |
| **Track** | AI |
| **Team Lead** | Het Baraiya — 24dce008@charusat.edu.in |
| **Members** | Harsh Bhalodiya, Tirth Gambhava, Ujjain Dhanani |

---

## 🎯 Problem Statement


Semiconductor process and yield engineers face sudden wafer lot rejections and yield drops caused by complex multi-variable sensor drift and equipment anomalies across manufacturing steps. Diagnosing root causes manually is slow, reactive, and error-prone, resulting in high scrap costs, material waste, and prolonged tool downtime. WafGuard AI automates early risk detection and pinpoints potential root causes before wafers complete processing.

---

## 💡 Solution


WafGuard AI is an AI-powered decision-support platform that unifies tabular equipment sensor telemetry, spatial wafer die-map pattern analysis, and historical yield data. It leverages XGBoost yield regression/risk scoring, PyTorch CNNs with Grad-CAM visual explainability, and multi-signal SHAP attribution to deliver actionable, evidence-grounded engineering recommendations.

---

## ✨ Key Features

- **Feature 1:** **Predictive Yield & Risk Scoring:** Real-time lot yield regression and low-yield risk classification using XGBoost and multi-factor weighted scoring.
- **Feature 2:** **Multi-Signal Root Cause Analysis:** SHAP attribution combined with deviation analysis, historical defect tracking, and equipment status ranking.
- **Feature 3:** **Wafer Map Pattern & Defect CNN:** OpenCV spatial feature extraction & PyTorch CNN classifying wafer failure patterns with Grad-CAM visual heatmaps.
- **Feature 4:** **Interactive What-If Simulator:** Parameter perturbation engine allowing yield engineers to simulate recipe parameter adjustments before running lots.
- **Feature 5:** **Engineer Copilot & Control Room Dashboard:** 14-route React dashboard featuring Recharts/Plotly analytics and an evidence-grounded Copilot Q&A service.

---

## 🛠️ Tech Stack

| Category | Technologies |
|---|---|
| **Languages** | Python 3.13, JavaScript (ES6+ / JSX), HTML5, CSS3 |
| **Frameworks** | FastAPI, React (Vite), PyTorch, scikit-learn, XGBoost |
| **IBM Technologies** | watsonx.ai (LLM copilot integration ready), IBM Cloud deployment ready |
| **Databases** | MongoDB (PyMongo) |
| **Other** | OpenCV, SHAP, Recharts, Plotly.js, Tailwind CSS, Pytest, Vitest |

---

## 📁 Repository Structure

```
├── backend/               # FastAPI application backend, ML models, & services
│   ├── app/               # Core API routes, DB repositories, & ML pipelines
│   ├── models/            # Model artifacts and versioning metadata
│   ├── scripts/           # Data downloads, synthetic generation, training & seeding
│   └── tests/             # Pytest backend test suite
├── frontend/              # React (Vite) + Tailwind CSS dashboard interface
│   ├── src/               # Page routes, reusable components, & API services
│   └── public/            # Static assets
├── data/                  # SECOM, WM-811K, and synthetic manufacturing data
├── docs/                  # Architecture & methodology documentation
│   └── risk_and_rootcause_methodology.md
├── notebooks/             # Exploratory data analysis & model development notebooks
└── README.md              # Project README documentation
```

---

## ⚡ How to Run


```bash
# 1. Clone the repo
git clone https://github.com/wafguard/wafguard-ai.git
cd wafguard-ai

# 2. Install dependencies
# Backend
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# Frontend
cd ../frontend
npm install

# 3. Configure environment
cd ..
cp .env.example .env
# Edit .env with your MongoDB and service configurations

# 4. Seed database, train models, and run the project
# Ensure local MongoDB is running (mongodb://localhost:27017)
cd backend
python scripts/download_secom.py
python scripts/generate_synthetic_data.py
python scripts/seed_database.py
python scripts/train_yield_model.py
python scripts/train_wafer_model.py

# Start Backend API Server:
uvicorn app.main:app --reload

# Start Frontend Dev Server (in a separate terminal):
cd ../frontend
npm run dev
```

---

## 🖥️ Demo

| Artifact | Link |
|---|---|
| 📹 Demo Video | [See demo/demo-video-link.txt](demo/demo-video-link.txt) |
| 🌐 Live Demo | [See demo/live-demo-url.txt](demo/live-demo-url.txt) |
| 🖼️ Screenshots | [See demo/screenshots/](demo/screenshots/) |
| 📊 Presentation | [See presentation/slides.pdf](presentation/) |

---

## ⚠️ Known Limitations


- UCI SECOM and WM-811K are public datasets from different sources; tabular telemetry and wafer-map spatial patterns are linked via a synthetic manufacturing layer.
- Wafer-map pattern classification defaults to training on procedurally generated synthetic maps unless Kaggle's `LSWMD.pkl` dataset is manually supplied.
- Root-cause rankings provide model-attribution candidates (SHAP + multi-factor deviation heuristic) rather than proven causal mechanisms.
- The user interface is currently optimized for dark "control-room" operational display modes.

---

## 🏅 What We're Most Proud Of

We are most proud of building an end-to-end, multi-modal decision support system that bridges complex tabular sensor streams, spatial visual wafer map failure modes (PyTorch CNN + Grad-CAM), and explainable SHAP multi-signal scoring into an interactive 14-route engineer dashboard. Every insight, risk score, and recommended engineering action (such as resolving `LOT_10901`'s RF-power drift on equipment `ETCH_007`) is dynamically derived from live model inferences and deterministic scoring rules rather than static hardcoded mock data.

---
