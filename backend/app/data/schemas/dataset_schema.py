"""
Cross-dataset contract (spec section 4-5; deep-research report section 6).

SECOM (numerical process/yield ML) and WM-811K (wafer-map vision) are
unrelated public datasets — different fabs, different eras, no shared
lot/wafer identifiers. WafGuard never merges them as if they were one
fab's records. Instead, every record that reaches the API carries an
explicit `data_source` / `data_provenance` tag from this module, and the
frontend is expected to render `DATA_PROVENANCE_LABELS[...]` next to any
number derived from a non-real source.

The only thing genuinely shared across sources is this minimal identifier
contract, used so the synthetic demo layer can plug into the same
Lot/Wafer/Equipment shapes the real datasets would eventually populate.
"""
from typing import Literal

DataSource = Literal["secom", "wm811k", "synthetic"]

COMMON_IDENTIFIER_FIELDS = ["lot_id", "wafer_id", "equipment_id", "process_step", "timestamp"]

DATA_PROVENANCE_LABELS: dict[str, str] = {
    "secom": "Public semiconductor manufacturing dataset used for prototype modeling (UCI SECOM).",
    "wm811k": "Public wafer-map dataset (WM-811K).",
    "wm811k_synthetic": "DEMO / SIMULATED DATA — procedurally generated wafer maps mimicking the WM-811K pattern taxonomy.",
    "synthetic": "DEMO / SIMULATED DATA — synthetic manufacturing records generated for this prototype.",
}


def provenance_label(source: str) -> str:
    return DATA_PROVENANCE_LABELS.get(source, "Unknown data source.")
