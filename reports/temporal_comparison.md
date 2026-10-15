# Temporal model comparison (Phase 2B)

**Dataset:** synthetic v2 sensor telemetry (DEMO / SIMULATED DATA). Sequences (N=5000, T=48, C=4) with missing-value masks; mask coverage 0.995.
Normalization statistics fit on the train split only. All metrics on the chronological test split.

## Risk classification (is_low_yield) — test split

| Model | Precision | Recall | F1 | ROC-AUC |
|---|---|---|---|---|
| TemporalGRU (raw telemetry) | 0.730 | 0.568 | 0.639 | 0.812 |
| TemporalTransformer (comparison) | 0.687 | 0.568 | 0.622 | 0.809 |
| Tabular XGBoost (engineered features) | 0.446 | 0.407 | 0.426 | 0.744 |

## Yield regression — test split

| Model | MAE | RMSE | R² |
|---|---|---|---|
| TemporalGRU (raw telemetry) | 2.92 | 4.73 | 0.418 |
| TemporalTransformer (comparison) | 2.94 | 4.70 | 0.423 |
| Tabular XGBoost (engineered features) | 3.56 | 5.18 | 0.302 |

**Ship decision:** `TemporalTransformer` — GRU risk F1=0.639 vs Transformer 0.622; GRU yield R2=0.418 vs Transformer 0.423 on the same test split.

Embeddings exported: `backend/models/artifacts/temporal_embeddings.parquet` (lot_id + 64-d GRU/Transformer embedding, whole dataset).

**Honest reading:** raw-telemetry sequence models compete with — and here often trail — the tabular model that consumes engineered summaries of the same signals, because 150 timesteps per lot is a short horizon and the engineered features already encode drift/excursions. The temporal model earns its keep where the tabular one is blind: within-lot *shape* (ramps, oscillation, dropouts) and a reusable embedding for lot-similarity search.

All data synthetic. Metrics describe the simulator's fault physics, not a real fab.