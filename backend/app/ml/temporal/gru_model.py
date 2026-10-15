"""
Mask-aware GRU for lot-level risk + yield (Phase 2B).

One encoder, two heads:
  - risk_head:  logit for is_low_yield (BCEWithLogitsLoss)
  - yield_head: predicted yield % (MSELoss)
  - embedding:  the GRU's final hidden state (hidden_dim), exported per lot
    for similarity search / downstream clustering.

Masking semantics: inputs are zeroed where mask==0 AND the sequence is
truncated to each lot's last real timestep before the final state is
taken, so a lot whose sensor stream ended early (sensor_dropout) is
encoded from the data that actually exists, not from padding.
"""
import numpy as np
import torch
import torch.nn as nn


class TemporalGRU(nn.Module):
    def __init__(self, n_channels: int = 4, hidden_dim: int = 64, num_layers: int = 1, dropout: float = 0.1):
        super().__init__()
        self.hidden_dim = hidden_dim
        self.gru = nn.GRU(
            input_size=n_channels,
            hidden_size=hidden_dim,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )
        self.dropout = nn.Dropout(dropout)
        self.risk_head = nn.Linear(hidden_dim, 1)
        self.yield_head = nn.Linear(hidden_dim, 1)

    def forward(self, x: torch.Tensor, mask: torch.Tensor | None = None):
        """
        x: (B, T, C); mask: (B, T, C) with 1.0 = real reading.
        Returns (risk_logit, yield_pred, embedding) where embedding is (B, hidden_dim).
        """
        if mask is not None:
            x = x * mask  # missing readings are absence, not zeros-as-signal
            # Timestep exists if ANY channel has a real reading there.
            step_exists = (mask.sum(dim=2) > 0).long()  # (B, T)
            lengths = step_exists.sum(dim=1).clamp(min=1)  # (B,)
        else:
            lengths = torch.full((x.size(0),), x.size(1), dtype=torch.long, device=x.device)

        packed = nn.utils.rnn.pack_padded_sequence(
            x, lengths.cpu(), batch_first=True, enforce_sorted=False
        )
        _, h_n = self.gru(packed)  # h_n: (num_layers, B, hidden)
        embedding = h_n[-1]  # last layer's final hidden state
        embedding = self.dropout(embedding)
        risk_logit = self.risk_head(embedding).squeeze(-1)
        yield_pred = self.yield_head(embedding).squeeze(-1)
        return risk_logit, yield_pred, embedding


def train_temporal_model(
    model: nn.Module,
    train_tensors: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray],
    val_tensors: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray],
    task: str = "risk",
    epochs: int = 30,
    batch_size: int = 64,
    lr: float = 1e-3,
    patience: int = 5,
    device: str = "cpu",
    seed: int = 42,
) -> dict:
    """
    train/val tensors: (X, mask, risk_labels, yield_labels).
    task: "risk" (BCE) or "yield" (MSE). Early stopping on validation loss.
    Returns history + best-state info; the caller gets back the model with
    the best validation weights restored.
    """
    torch.manual_seed(seed)
    X_tr, mask_tr, risk_tr, yield_tr = train_tensors
    X_va, mask_va, risk_va, yield_va = val_tensors

    def make_ds(X, mask, risk, yield_):
        return torch.utils.data.TensorDataset(
            torch.from_numpy(X).float(),
            torch.from_numpy(mask).float(),
            torch.from_numpy(np.asarray(risk, dtype=np.float32)),
            torch.from_numpy(np.asarray(yield_, dtype=np.float32)),
        )

    train_loader = torch.utils.data.DataLoader(
        make_ds(X_tr, mask_tr, risk_tr, yield_tr), batch_size=batch_size, shuffle=True
    )
    val_loader = torch.utils.data.DataLoader(
        make_ds(X_va, mask_va, risk_va, yield_va), batch_size=batch_size, shuffle=False
    )

    model = model.to(device)
    criterion = nn.BCEWithLogitsLoss() if task == "risk" else nn.MSELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)

    best_val_loss = float("inf")
    best_epoch = -1
    best_state = None
    history = {"train_loss": [], "val_loss": [], "early_stopped": False, "best_epoch": None}
    for epoch in range(epochs):
        model.train()
        running = 0.0
        for xb, mb, rb, yb in train_loader:
            xb, mb, rb, yb = xb.to(device), mb.to(device), rb.to(device), yb.to(device)
            targets = rb if task == "risk" else yb
            optimizer.zero_grad()
            risk_logit, yield_pred, _ = model(xb, mb)
            loss = criterion(risk_logit if task == "risk" else yield_pred, targets)
            loss.backward()
            optimizer.step()
            running += loss.item() * xb.size(0)
        train_loss = running / len(train_loader.dataset)

        model.eval()
        val_running = 0.0
        with torch.no_grad():
            for xb, mb, rb, yb in val_loader:
                xb, mb, rb, yb = xb.to(device), mb.to(device), rb.to(device), yb.to(device)
                targets = rb if task == "risk" else yb
                risk_logit, yield_pred, _ = model(xb, mb)
                loss = criterion(risk_logit if task == "risk" else yield_pred, targets)
                val_running += loss.item() * xb.size(0)
        val_loss = val_running / len(val_loader.dataset)

        history["train_loss"].append(round(train_loss, 5))
        history["val_loss"].append(round(val_loss, 5))
        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_epoch = epoch
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
        if epoch - best_epoch >= patience:
            history["early_stopped"] = True
            break

    if best_state is not None:
        model.load_state_dict(best_state)
    history["best_epoch"] = best_epoch
    return history


@torch.no_grad()
def predict_temporal(model: nn.Module, X: np.ndarray, mask: np.ndarray, task: str = "risk", device: str = "cpu"):
    """Returns (probabilities_or_values, embeddings) for a batch of sequences."""
    model = model.to(device)
    model.eval()
    x = torch.from_numpy(X).float().to(device)
    m = torch.from_numpy(mask).float().to(device)
    risk_logit, yield_pred, embedding = model(x, m)
    if task == "risk":
        out = torch.sigmoid(risk_logit)
    else:
        out = yield_pred
    return out.cpu().numpy(), embedding.cpu().numpy()
