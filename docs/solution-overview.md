# Solution Overview

## What We Built

WafGuard AI is an intelligent decision-support system built for semiconductor process and yield engineers. It predicts wafer lot yield drops, ranks potential root causes of manufacturing anomalies, analyzes wafer visual defect maps, and recommends corrective engineering actions *before* wafers complete processing. 

By bringing together tabular machine telemetry, spatial wafer die patterns, and equipment health metrics into a single unified platform, WafGuard AI transforms raw manufacturing logs into clear, evidence-grounded engineering recommendations that prevent scrap and reduce tool downtime.

---

## How It Works

1. **Data Ingestion & Preprocessing:** Tabular equipment sensor readings (temperature, chamber pressure, RF power, gas flows) and spatial wafer die grids are ingested into the system from fab process tools and manufacturing datasets.
2. **Tabular Yield & Risk Prediction:** The FastAPI backend runs XGBoost ML models over tabular feature vectors to predict lot yield percentages and assign multi-factor low-yield risk levels (LOW, MEDIUM, HIGH).
3. **Spatial Wafer Pattern Classification & Heatmaps:** Wafer die maps are preprocessed via OpenCV and classified by a PyTorch Convolutional Neural Network (CNN) into pattern categories (e.g., Edge-Ring, Center, Scratch). Grad-CAM visually highlights physical defect hotspots on the wafer map.
4. **SHAP Multi-Signal Root Cause Ranking:** A multi-signal attribution engine combines model SHAP values, parameter deviations from historical baselines, equipment maintenance logs, and spatial pattern correlations to rank root-cause candidates by probability.
5. **Interactive Dashboard & Copilot:** Yield engineers investigate incident stories across a 14-route React dashboard, run recipe parameter adjustments using a What-If simulator, and query an evidence-grounded Copilot powered by watsonx.ai for natural-language Q&A.
6. **Automated Alerting:** When high-risk lots or critical parameter drifts are detected, real-time alerts are pushed directly to fab engineers via Slack Webhooks.

---

## Architecture Diagram

See [`architecture.md`](architecture.md) for the detailed system architecture.

```
[User / Engineer] → [Frontend: React 18] → [Backend: FastAPI] → [watsonx.ai Copilot] → [Dashboard / Alert]
                                                  ↓
                                         [MongoDB Repository]
```

---

## Key Design Decisions

| Decision | Rationale |
|---|---|
| **Multi-Signal Root-Cause Scoring (SHAP + Domain Signals)** | Pure ML feature importance lacks physical fab context. Combining SHAP attribution with parameter deviation, equipment maintenance status, and spatial pattern rules yields explainable candidate root causes rather than opaque predictions. |
| **Modal Separation (XGBoost + PyTorch CNN)** | Semiconductor fab data features distinct data types — tabular sensor timeseries and spatial wafer die grids. Specialized XGBoost models handle tabular telemetry while a PyTorch CNN handles visual wafer pattern maps before their outputs are merged. |
| **Evidence-Grounded Copilot Guardrails** | Generative LLMs can hallucinate diagnoses. The copilot constructs deterministic evidence bundles (tagged as FACT, MODEL_OUTPUT, or INFERENCE) and uses watsonx.ai strictly to explain verified evidence, never allowing the LLM to invent facts. |
| **Synchronous PyMongo & FastAPI Threadpools** | ML model inference runs CPU-bound matrix operations. Using synchronous PyMongo and standard FastAPI routes executes inference in threadpools, preventing event loop blocking without worker queue complexity. |

---

## IBM Technologies Used

- **IBM watsonx.ai:** Integrated into the Engineer Copilot service (`backend/app/services/copilot_service.py`) via the IBM Watson Machine Learning SDK / watsonx APIs. It ingests verified multi-signal evidence bundles (predicted yield loss, SHAP feature attribution, parameter drift metrics, and visual wafer defect classes) to synthesize natural-language engineering reports and actionable recommendations.
- **IBM Cloud:** Architecture designed for deployment on IBM Cloud microservice infrastructure, supporting containerized API routing, MongoDB enterprise data storage, and scalable AI model serving.
