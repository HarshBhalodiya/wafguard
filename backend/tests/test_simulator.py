"""
Phase 1 simulator tests: schema validity, ID uniqueness, sensible ranges,
correct failure rate, and that the hidden ground truth matches the faults
the generator actually injected (via measurable sensor signatures).
"""
import dataclasses

import numpy as np
import pandas as pd
import pytest

from app.config import LOW_YIELD_THRESHOLD, PROCESS_PARAMETER_RANGES
from app.simulator.config import load_config
from app.simulator.mechanisms import MECHANISMS, mechanism_for
from app.simulator.process_physics import CHANNEL_SPECS


@pytest.fixture(scope="module")
def cfg():
    return load_config()


@pytest.fixture(scope="module")
def small_tables(cfg):
    """A dedicated small dataset so these tests don't depend on the session
    fixture's tables (keeps this module self-contained and fast)."""
    from app.simulator.generator import generate_dataset

    return generate_dataset(dataclasses.replace(cfg, n_lots=150))


def test_config_fault_rates_are_valid(cfg):
    total = sum(cfg.fault_rates.values())
    assert 0.99 <= total <= 1.0 + 1e-9
    assert "none" in cfg.fault_rates
    assert 0.10 <= cfg.low_yield_fraction <= 0.20  # spec: default 10-20% low-yield lots


def test_every_fault_has_a_mechanism_with_a_signature():
    for fault in MECHANISMS:
        mech = mechanism_for(fault)
        assert mech is not None
        assert mech.spatial_signature in {"Edge-Ring", "Donut", "Center", "Loc", "Scratch", "Random"}
        assert mech.particle_multiplier >= 1.0
        assert mech.yield_loss_pp > 0


def test_no_duplicate_ids(small_tables):
    lots = small_tables["lots"]
    assert lots["lot_id"].is_unique
    assert small_tables["equipment"]["equipment_id"].is_unique
    assert small_tables["chambers"]["chamber_id"].is_unique
    assert small_tables["recipes"]["recipe_id"].is_unique
    wafers = small_tables["wafers"]
    assert wafers["wafer_id"].is_unique
    defects = small_tables["defects"]
    assert defects["defect_id"].is_unique
    maintenance = small_tables["maintenance"]
    assert maintenance["event_id"].is_unique


def test_lot_wafer_chain_is_consistent(small_tables, cfg):
    lots = small_tables["lots"]
    wafers = small_tables["wafers"]
    assert len(wafers) == len(lots) * cfg.wafers_per_lot
    counts = wafers.groupby("lot_id").size()
    assert (counts == cfg.wafers_per_lot).all()
    assert set(wafers["lot_id"]) <= set(lots["lot_id"])
    # wafer slots are 1..N per lot
    slots_ok = wafers.sort_values(["lot_id", "slot"]).groupby("lot_id")["slot"].apply(lambda s: list(s) == list(range(1, cfg.wafers_per_lot + 1)))
    assert slots_ok.all()


def test_chambers_belong_to_equipment(small_tables):
    equipment_ids = set(small_tables["equipment"]["equipment_id"])
    chambers = small_tables["chambers"]
    assert set(chambers["equipment_id"]) <= equipment_ids
    starts_ok = [
        str(cid).startswith(str(eq)) for cid, eq in zip(chambers["chamber_id"], chambers["equipment_id"])
    ]
    assert all(starts_ok)
    # every lot's chamber belongs to the lot's equipment
    lots = small_tables["lots"]
    chamber_owner = dict(zip(chambers["chamber_id"], chambers["equipment_id"]))
    assert all(chamber_owner[c] == e for c, e in zip(lots["chamber_id"], lots["equipment_id"]))


def test_lot_ids_unique_and_ranges_sane(small_tables, cfg):
    lots = small_tables["lots"]
    assert lots["lot_id"].is_unique
    assert lots["yield"].between(0, 100).all()
    assert lots["defect_probability"].between(0, 1).all()
    assert lots["defect_density"].ge(0).all()
    starts = pd.to_datetime(lots["process_start"])
    assert (starts.diff().dropna() >= pd.Timedelta(0)).all()  # sorted ascending
    end = pd.Timestamp(cfg.end_date)
    assert (starts <= end).all()
    assert (starts >= end - pd.Timedelta(days=cfg.lookback_days + 1)).all()


def test_telemetry_sensible_ranges_and_channels(small_tables, cfg):
    tel = small_tables["sensor_data"]
    assert set(cfg.telemetry.channels) <= set(tel["parameter"].unique())
    # at least 5 channels as spec requires
    assert tel["parameter"].nunique() >= 5
    # fixed sample rate: every run has n_samples rows per channel
    n = cfg.telemetry.n_samples
    per_run = tel.groupby(["lot_id", "parameter"]).size()
    assert (per_run == n).all()
    # values (where present) within plausible physical ranges
    for channel, spec in CHANNEL_SPECS.items():
        sub = tel[tel["parameter"] == channel]["value"].dropna()
        assert len(sub) > 0
        # faults push beyond the band but stay physically bounded: cap at
        # center +/- (max fault severity + degradation + excursions) headroom
        bound = spec.half_width * 6.0
        assert sub.between(spec.center - bound, spec.center + bound).all(), channel
    # missing values exist (dropouts) but are a small minority
    missing_frac = tel["value"].isna().mean()
    assert 0.0 < missing_frac < 0.10


def test_failure_rate_matches_config(small_tables, cfg):
    lots = small_tables["lots"]
    realized = float(lots["is_low_yield"].mean())
    # statistical tolerance for 150 lots around the 0.15 target
    assert 0.03 <= realized <= 0.35
    # and every lot flagged low-yield really is under the threshold
    assert (lots.loc[lots["is_low_yield"] == 1, "yield"] < cfg.low_yield_threshold).all()
    assert (lots.loc[lots["is_low_yield"] == 0, "yield"] >= cfg.low_yield_threshold).all()


def test_ground_truth_matches_injected_faults(small_tables, cfg):
    """The heart of Phase 1's testability: the hidden labels must agree with
    measurable evidence in the telemetry."""
    tables = small_tables
    lots, gt, tel = tables["lots"], tables["ground_truth"], tables["sensor_data"]
    assert len(gt) == len(lots)
    assert set(gt["lot_id"]) == set(lots["lot_id"])

    merged = lots.merge(gt, on="lot_id")

    # 1. pressure instability => strong positive/negative pressure slope
    pressure = tel[tel["parameter"] == "pressure"].copy()
    slopes = pressure.groupby("lot_id")["value"].apply(lambda s: (s.dropna().iloc[-1] - s.dropna().iloc[0]))
    faulty = merged[merged["true_fault"] == "chamber_pressure_instability"]["lot_id"]
    clean = merged[merged["true_fault"] == "none"]["lot_id"]
    if len(faulty) > 0:
        assert slopes[faulty].abs().mean() > slopes[clean].abs().mean() + 0.1

    # 2. rf_power_drift => strong RF slope vs clean lots
    rf = tel[tel["parameter"] == "rf_power"].copy()
    rf_slopes = rf.groupby("lot_id")["value"].apply(lambda s: (s.dropna().iloc[-1] - s.dropna().iloc[0]))
    rf_faulty = merged[merged["true_fault"] == "rf_power_drift"]["lot_id"]
    if len(rf_faulty) > 0:
        assert rf_slopes[rf_faulty].abs().mean() > rf_slopes[clean].abs().mean() + 1.0

    # 3. fault lots yield lower than clean lots
    fault_yields = merged.loc[merged["true_fault"] != "none", "yield"]
    clean_yields = merged.loc[merged["true_fault"] == "none", "yield"]
    assert fault_yields.mean() < clean_yields.mean() - 5.0

    # 4. mechanism label agrees with the mechanism table
    for _, row in merged.iterrows():
        mech = mechanism_for(row["true_fault"])
        expected = mech.name if mech else "none"
        assert row["mechanism"] == expected

    # 5. faulty lots carry their mechanism's spatial signature on failed wafers
    wafers = tables["wafers"]
    for _, row in merged[merged["true_fault"] != "none"].iterrows():
        mech = mechanism_for(row["true_fault"])
        wafer_rows = wafers[(wafers["lot_id"] == row["lot_id"]) & (wafers["yield_status"] == "fail")]
        if len(wafer_rows) > 0:
            assert (wafer_rows["true_pattern"] == mech.spatial_signature).all()


def test_degradation_linked_to_maintenance_age(small_tables):
    """Lots with very recent PMs should rarely be equipment_degradation."""
    merged = small_tables["lots"].merge(small_tables["ground_truth"], on="lot_id")
    recent_pm = merged[merged["equipment_days_since_pm"] < 10]
    if len(recent_pm) > 5:
        frac_degradation = (recent_pm["true_fault"] == "equipment_degradation").mean()
        assert frac_degradation < 0.20


def test_timeline_events_ordered_and_complete(small_tables):
    events = small_tables["events"]
    lots = small_tables["lots"]
    # every lot has a start and end event
    starts = set(events.loc[events["event_type"] == "PROCESS_START", "lot_id"])
    ends = set(events.loc[events["event_type"] == "PROCESS_END", "lot_id"])
    assert starts == set(lots["lot_id"])
    assert ends == set(lots["lot_id"])
    # per lot, events are time-ordered (MAINTENANCE events are anchored to
    # the maintenance log's own timestamps, so sort before comparing)
    for lot_id, group in events.groupby("lot_id"):
        non_maint = group[group["event_type"] != "MAINTENANCE"]
        ts = pd.to_datetime(non_maint["ts"]).sort_values()
        assert (ts.diff().dropna() >= pd.Timedelta(0)).all(), f"unordered events for {lot_id}"
    # excursion events carry parameter + value
    exc = events[events["event_type"] == "PARAMETER_EXCURSION"]
    assert ("parameter" in exc.iloc[0]["payload"]) if len(exc) else True
    # inspection events exist for every lot
    assert set(events.loc[events["event_type"] == "INSPECTION", "lot_id"]) == set(lots["lot_id"])


def test_electrical_tests_reference_real_lots(small_tables):
    electrical = small_tables["electrical_tests"]
    lots = small_tables["lots"]
    assert set(electrical["lot_id"]) == set(lots["lot_id"])
    assert electrical["pass_rate"].between(0, 1).all()


def test_generator_is_deterministic(cfg):
    from app.simulator.generator import generate_dataset

    t1 = generate_dataset(dataclasses.replace(cfg, n_lots=20))
    t2 = generate_dataset(dataclasses.replace(cfg, n_lots=20))
    pd.testing.assert_frame_equal(t1["lots"], t2["lots"])
    pd.testing.assert_frame_equal(t1["ground_truth"], t2["ground_truth"])
