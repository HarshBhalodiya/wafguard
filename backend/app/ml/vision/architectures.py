"""
Phase 2C comparison architectures for wafer-map classification.

The Phase 1 production model (app.ml.wafer_model.WaferCNN) stays; these are
the *comparison* candidates trained on the identical procedural dataset and
split by scripts/compare_vision_models.py:

  - SmallResNet: 3-conv stem + 2 residual blocks (~110K params)
  - SmallViT:    8x8 patch embedding + 2-layer encoder (~90K params)

All three expose the same forward contract as WaferCNN — return
(logits, spatial_features) — so Grad-CAM-style tooling and the embedding
extractor work identically across backbones.
"""
import torch
import torch.nn as nn


class ResidualBlock(nn.Module):
    def __init__(self, channels: int):
        super().__init__()
        self.conv1 = nn.Conv2d(channels, channels, 3, padding=1)
        self.bn1 = nn.BatchNorm2d(channels)
        self.conv2 = nn.Conv2d(channels, channels, 3, padding=1)
        self.bn2 = nn.BatchNorm2d(channels)
        self.act = nn.ReLU(inplace=True)

    def forward(self, x):
        out = self.act(self.bn1(self.conv1(x)))
        out = self.bn2(self.conv2(out))
        return self.act(out + x)  # identity residual


class SmallResNet(nn.Module):
    """Conv stem -> 2 residual blocks -> global pool -> linear head."""

    def __init__(self, num_classes: int, width: int = 64):
        super().__init__()
        self.stem = nn.Sequential(
            nn.Conv2d(3, 32, 3, padding=1), nn.BatchNorm2d(32), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(32, width, 3, padding=1), nn.BatchNorm2d(width), nn.ReLU(), nn.MaxPool2d(2),
        )
        self.res1 = ResidualBlock(width)
        self.res2 = ResidualBlock(width)
        self.pool = nn.AdaptiveAvgPool2d(1)
        self.dropout = nn.Dropout(0.2)
        self.fc = nn.Linear(width, num_classes)

    def forward(self, x):
        x = self.stem(x)
        x = self.res2(self.res1(x))  # (B, width, H/4, W/4) spatial features
        feats = x
        pooled = self.pool(x).flatten(1)
        logits = self.fc(self.dropout(pooled))
        return logits, feats


class SmallViT(nn.Module):
    """
    8x8 patch tokens + learned positional embedding + 2-layer encoder.
    'Features' are reshaped token embeddings from the last layer so the
    (B, C, h, w) contract holds for downstream tooling.
    """

    def __init__(self, num_classes: int, d_model: int = 96, nhead: int = 4,
                 num_layers: int = 2, dim_ff: int = 192, patch: int = 8, img_size: int = 64):
        super().__init__()
        assert img_size % patch == 0
        self.patch = patch
        self.grid = img_size // patch
        n_tokens = self.grid * self.grid
        self.proj = nn.Conv2d(3, d_model, kernel_size=patch, stride=patch)
        self.pos_emb = nn.Parameter(torch.zeros(1, n_tokens, d_model))
        nn.init.trunc_normal_(self.pos_emb, std=0.02)
        layer = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=nhead, dim_feedforward=dim_ff,
            dropout=0.1, batch_first=True,
        )
        self.encoder = nn.TransformerEncoder(layer, num_layers=num_layers)
        self.norm = nn.LayerNorm(d_model)
        self.dropout = nn.Dropout(0.2)
        self.fc = nn.Linear(d_model, num_classes)

    def forward(self, x):
        B = x.size(0)
        tokens = self.proj(x).flatten(2).transpose(1, 2)  # (B, N, d_model)
        h = self.encoder(tokens + self.pos_emb)
        h = self.norm(h)
        pooled = h.mean(dim=1)  # (B, d_model) — mean-token embedding
        logits = self.fc(self.dropout(pooled))
        feats = h.transpose(1, 2).reshape(B, -1, self.grid, self.grid)  # (B, d_model, g, g)
        return logits, feats
