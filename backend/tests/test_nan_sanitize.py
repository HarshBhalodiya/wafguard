"""
Regression tests for two production 500s found by `_probe_api.py`:

1. NaN/Infinity doubles reaching MongoDB. They are legal IEEE-754 and PyMongo
   stores them happily, but `json.dumps` refuses to serialize them, so every
   endpoint returning such a document died with
   `ValueError: Out of range float values are not JSON compliant: nan`
   (GET /api/v1/lots, and GET /api/v1/equipment/{id} through `recent_lots`).
   The fix scrubs them on the way into Mongo — see app/utils/mongo_sanitize.py.
2. An empty import body (`"lots": []`) builds a DataFrame with no columns, so
   `validate_tables` raised `KeyError: 'lot_id'` instead of validating nothing.

These tests are deliberately fixture-free (no simulator/training session
fixtures) so they stay a fast, always-runnable guard on both fixes.
"""
import math
from datetime import datetime, timezone

import mongomock
import numpy as np
import pandas as pd
import pytest

from app.database.repositories.base_repository import BaseRepository
from app.quality.validation import summarize, validate_tables
from app.utils.mongo_sanitize import scrub_nans


class _Lots(BaseRepository):
    collection_name = "lots"


@pytest.fixture
def repo():
    return _Lots(mongomock.MongoClient()["wafguard_test"])


# --------------------------------------------------------------------------- scrub_nans
def test_non_finite_floats_become_none():
    scrubbed = scrub_nans({"a": float("nan"), "b": float("inf"), "c": float("-inf"), "d": 1.5, "e": 0.0})
    assert scrubbed == {"a": None, "b": None, "c": None, "d": 1.5, "e": 0.0}


def test_scrub_reaches_nested_structures():
    """The real failure was nested: spatial_signature inside a lot document."""
    document = {
        "lot_id": "LOT_1",
        "spatial_signature": {"center": float("nan"), "zone": [float("inf"), 3.0, {"deep": float("nan")}]},
        "samples": (float("nan"), 2.0),
        "keep": {"ts": datetime(2026, 1, 1, tzinfo=timezone.utc), "ok": True},
    }
    scrubbed = scrub_nans(document)
    assert scrubbed["spatial_signature"] == {"center": None, "zone": [None, 3.0, {"deep": None}]}
    assert scrubbed["samples"] == (None, 2.0)
    assert scrubbed["keep"]["ts"] == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert scrubbed["keep"]["ok"] is True


def test_pandas_and_numpy_scalars_are_scrubbed():
    """The NaNs originate as empty CSV cells, i.e. pandas/numpy scalars."""
    values = scrub_nans([np.float64("nan"), np.float32("inf"), np.nan, pd.NaT, np.int64(7)])
    assert values[:4] == [None, None, None, None]
    assert int(values[4]) == 7


@pytest.mark.parametrize("value", ["text", 3, 2.5, True, None])
def test_scalar_passthrough(value):
    assert scrub_nans(value) == value


def test_scrubbed_document_is_json_serializable():
    import json

    scrubbed = scrub_nans({"spatial_signature": {"center": float("nan")}})
    assert json.loads(json.dumps(scrubbed)) == {"spatial_signature": {"center": None}}


# --------------------------------------------------------------------------- write path
def test_repository_insert_stores_null_instead_of_nan(repo):
    repo.insert_one({"lot_id": "LOT_1", "spatial_signature": {"center": float("nan"), "zone": float("inf")}})
    stored = repo.collection.find_one({"lot_id": "LOT_1"})
    assert stored["spatial_signature"] == {"center": None, "zone": None}
    assert all(not (isinstance(v, float) and not math.isfinite(v)) for v in stored["spatial_signature"].values())


def test_repository_update_stores_null_instead_of_nan(repo):
    repo.insert_one({"lot_id": "LOT_2", "spatial_signature": None})
    repo.update_one({"lot_id": "LOT_2"}, {"spatial_signature": {"center": float("nan")}})
    assert repo.collection.find_one({"lot_id": "LOT_2"})["spatial_signature"] == {"center": None}


def test_repository_insert_many_stores_null_instead_of_nan(repo):
    repo.insert_many([{"lot_id": "LOT_3", "yield_value": float("nan")}, {"lot_id": "LOT_4", "yield_value": 91.0}])
    stored = {doc["lot_id"]: doc["yield_value"] for doc in repo.collection.find({})}
    assert stored == {"LOT_3": None, "LOT_4": 91.0}


def test_repository_read_still_strips_object_id(repo):
    repo.insert_one({"lot_id": "LOT_5", "spatial_signature": {"center": float("nan")}})
    read = repo.find_one({"lot_id": "LOT_5"})
    assert "_id" not in read and read["spatial_signature"] == {"center": None}


# --------------------------------------------------------------------------- seeder write path
def test_seeder_batch_insert_stores_null_instead_of_nan():
    """The seeder is where the NaNs actually originated (pandas rows copied raw),
    so its batched insert must scrub too — not just the repository layer."""
    from seed_database import insert_many_batched

    collection = mongomock.MongoClient()["wafguard_test"]["lots"]
    records = [
        {"_id": "a", "lot_id": "LOT_A", "spatial_signature": {"center": float("nan"), "zone": float("inf")}},
        {"_id": "b", "lot_id": "LOT_B", "spatial_signature": {"center": 0.5}, "yield_value": float("nan")},
    ]
    assert insert_many_batched(collection, records, batch_size=1) == 2
    stored = {doc["lot_id"]: doc for doc in collection.find({})}
    assert stored["LOT_A"]["spatial_signature"] == {"center": None, "zone": None}
    assert stored["LOT_B"]["spatial_signature"] == {"center": 0.5}
    assert stored["LOT_B"]["yield_value"] is None


# --------------------------------------------------------------------------- empty batch
def test_empty_batch_validates_to_empty_report():
    """`"lots": []` used to raise KeyError: 'lot_id' -> a 500."""
    reports = validate_tables(pd.DataFrame([]), telemetry=None, expected_channels=[])
    assert len(reports) == 0
    assert "lot_id" in reports.columns
    assert summarize(reports) == {
        "n_lots": 0,
        "status_counts": {},
        "issues_per_check": {},
        "validator_version": summarize(reports)["validator_version"],
    }


def test_frame_without_lot_id_column_does_not_crash():
    """Any column-less frame (not just the import path) must not raise."""
    assert len(validate_tables(pd.DataFrame(columns=[]), telemetry=None, expected_channels=[])) == 0


def test_non_empty_batch_still_validates():
    """The guard must not short-circuit real batches."""
    lots = pd.DataFrame([{"lot_id": "LOT_9", "process_start": "2026-01-01T00:00:00Z", "process_end": "2026-01-01T02:00:00Z"}])
    reports = validate_tables(lots, telemetry=None, expected_channels=[])
    assert list(reports["lot_id"]) == ["LOT_9"]
    assert reports.iloc[0]["status"] == "PASS"
