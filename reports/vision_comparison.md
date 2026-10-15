# Wafer-map vision model comparison (Phase 2C)

> **SYNTHETIC-ONLY DATA.** Every wafer map here is procedurally generated (`app.data.loaders.synthetic_wafer_loader`, WM-811K-like taxonomy). These numbers describe the generator's pattern physics — they say nothing about performance on a real fab's maps.

Identical procedural dataset + stratified split for all models (train=1890 / val=405 / test=405, 9 classes, seed=99).

## Test-split results

| Model | Accuracy | Macro-F1 | Best val epoch |
|---|---|---|---|
| WaferCNN | 0.8963 | 0.8963 | 14 |
| SmallResNet | 0.7432 | 0.6686 | 12 |
| SmallViT | 0.9086 | 0.9084 | 14 |

## Per-class recall / F1 — test split

### WaferCNN

| Class | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| Center | 0.868 | 0.733 | 0.795 | 45 |
| Donut | 1.000 | 0.933 | 0.966 | 45 |
| Edge-Loc | 1.000 | 1.000 | 1.000 | 45 |
| Edge-Ring | 1.000 | 1.000 | 1.000 | 45 |
| Loc | 0.644 | 0.644 | 0.644 | 45 |
| Random | 0.886 | 0.867 | 0.876 | 45 |
| Scratch | 0.933 | 0.933 | 0.933 | 45 |
| Near-full | 1.000 | 1.000 | 1.000 | 45 |
| None | 0.768 | 0.956 | 0.851 | 45 |

### SmallResNet

| Class | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| Center | 0.577 | 1.000 | 0.732 | 45 |
| Donut | 1.000 | 0.978 | 0.989 | 45 |
| Edge-Loc | 0.455 | 1.000 | 0.625 | 45 |
| Edge-Ring | 1.000 | 1.000 | 1.000 | 45 |
| Loc | 0.000 | 0.000 | 0.000 | 45 |
| Random | 1.000 | 0.711 | 0.831 | 45 |
| Scratch | 0.726 | 1.000 | 0.841 | 45 |
| Near-full | 1.000 | 1.000 | 1.000 | 45 |
| None | 0.000 | 0.000 | 0.000 | 45 |

### SmallViT

| Class | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| Center | 0.933 | 0.933 | 0.933 | 45 |
| Donut | 1.000 | 1.000 | 1.000 | 45 |
| Edge-Loc | 0.978 | 1.000 | 0.989 | 45 |
| Edge-Ring | 1.000 | 1.000 | 1.000 | 45 |
| Loc | 0.721 | 0.689 | 0.705 | 45 |
| Random | 0.857 | 0.800 | 0.828 | 45 |
| Scratch | 0.932 | 0.911 | 0.921 | 45 |
| Near-full | 1.000 | 1.000 | 1.000 | 45 |
| None | 0.760 | 0.844 | 0.800 | 45 |

Confusion matrices: [WaferCNN](confusion_matrix_wafercnn.png) · [SmallResNet](confusion_matrix_smallresnet.png) · [SmallViT](confusion_matrix_smallvit.png)

**Best on macro-F1: SmallViT.** The shipped production artifact stays `wafer_pattern_cnn` (the runtime loader constructs a WaferCNN); switching architectures would require a loader change and is logged as a decision, not done silently.

Embeddings from the best encoder for all val+test maps: `backend/models/artifacts/wafer_embeddings.npz` (embeddings + labels + ids), consumable by lot-similarity search without re-running the backbone per query.