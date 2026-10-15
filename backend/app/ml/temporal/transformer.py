"""
Small temporal Transformer — the Phase 2B *comparison* model.

Trained on the SAME chronological split as the GRU (see
scripts/train_temporal_model.py). Spec rule: the Transformer ships as the
production encoder only if it beats the GRU on the same test split;
otherwise it stays in the report as a comparison and the GRU ships.
"""
import numpy as np
import torch
import torch.nn as nn


class TemporalTransformer(nn.Module):
    def __init__(self, n_channels: int = 4, d_model: int = 64, nhead: int = 4,
                 num_layers: int = 2, dim_ff: int = 128, dropout: float = 0.1,
                 max_len: int = 150):
        super().__init__()
        self.input_proj = nn.Linear(n_channels, d_model)
        self.pos_emb = nn.Parameter(torch.zeros(1, max_len, d_model))
        nn.init.trunc_normal_(self.pos_emb, std=0.02)
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=nhead, dim_feedforward=dim_ff,
            dropout=dropout, batch_first=True,
        )
        self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
        self.risk_head = nn.Linear(d_model, 1)
        self.yield_head = nn.Linear(d_model, 1)

    def forward(self, x: torch.Tensor, mask: torch.Tensor | None = None):
        """
        x: (B, T, C); mask: (B, T, C). Masked timesteps are zeroed before the
        input projection and excluded from masked mean-pooling.
        Returns (risk_logit, yield_pred, embedding).
        """
        B, T, _ = x.shape
        if mask is not None:
            x = x * mask
            step_exists = (mask.sum(dim=2) > 0).float()  # (B, T)
        else:
            step_exists = torch.ones(B, T, device=x.device)

        h = self.input_proj(x) + self.pos_emb[:, :T, :]
        # key_padding_mask: True = ignore that timestep
        key_padding_mask = step_exists == 0
        h = self.encoder(h, src_key_padding_mask=key_padding_mask)

        # Masked mean pool over real timesteps.
        weights = step_exists.unsqueeze(-1)  # (B, T, 1)
        pooled = (h * weights).sum(dim=1) / weights.sum(dim=1).clamp(min=1.0)
        risk_logit = self.risk_head(pooled).squeeze(-1)
        yield_pred = self.yield_head(pooled).squeeze(-1)
        return risk_logit, yield_pred, pooled
