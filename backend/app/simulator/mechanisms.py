"""
Causal mechanism layer.

This is the module that makes the dataset *evaluable*: every fault has a
mechanism, every mechanism produces a spatial signature on the wafer and a
defect-density profile, and both the mechanism and the signature are
written to ground truth. Nothing downstream (root-cause engine, evidence
graph, similarity) can invent these links — they are fixed here, once,
with named constants so tests can assert them.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Mechanism:
    """How a fault damages dies, and what it looks like in inspection."""
    name: str
    particle_multiplier: float          # multiplies the background defect rate
    base_defect_probability: float      # per-wafer probability of a patterned fail
    spatial_signature: str              # WM-811K-style pattern the mechanism paints
    edge_bias: float                    # 0 = uniform across the wafer
    scratch_bias: float                 # probability mass moved to a scratch line
    yield_loss_pp: float                # percentage points lost when the mechanism is active


MECHANISMS = {
    "chamber_pressure_instability": Mechanism(
        name="chamber_pressure_instability",
        particle_multiplier=7.0,
        base_defect_probability=0.45,
        spatial_signature="Edge-Ring",
        edge_bias=0.65,
        scratch_bias=0.0,
        yield_loss_pp=10.0,
    ),
    "rf_power_drift": Mechanism(
        name="rf_power_drift",
        particle_multiplier=5.5,
        base_defect_probability=0.40,
        spatial_signature="Edge-Ring",
        edge_bias=0.55,
        scratch_bias=0.05,
        yield_loss_pp=8.5,
    ),
    "sensor_drift": Mechanism(
        name="sensor_drift",
        particle_multiplier=3.0,
        base_defect_probability=0.30,
        spatial_signature="Donut",
        edge_bias=0.30,
        scratch_bias=0.0,
        yield_loss_pp=7.0,
    ),
    "equipment_degradation": Mechanism(
        name="equipment_degradation",
        particle_multiplier=6.0,
        base_defect_probability=0.42,
        spatial_signature="Scratch",
        edge_bias=0.35,
        scratch_bias=0.55,
        yield_loss_pp=5.5,
    ),
    "sensor_dropout": Mechanism(
        name="sensor_dropout",
        particle_multiplier=2.0,
        base_defect_probability=0.14,
        spatial_signature="Random",
        edge_bias=0.0,
        scratch_bias=0.0,
        yield_loss_pp=3.5,
    ),
    "recipe_transition_problem": Mechanism(
        name="recipe_transition_problem",
        particle_multiplier=4.5,
        base_defect_probability=0.36,
        spatial_signature="Loc",
        edge_bias=0.20,
        scratch_bias=0.10,
        yield_loss_pp=6.5,
    ),
    "contamination_event": Mechanism(
        name="contamination_event",
        particle_multiplier=13.0,
        base_defect_probability=0.52,
        spatial_signature="Donut",
        edge_bias=0.25,
        scratch_bias=0.0,
        yield_loss_pp=13.0,
    ),
}


def mechanism_for(fault_type: str) -> Mechanism | None:
    """The (single) mechanism a fault type drives. 'none' has no mechanism."""
    return MECHANISMS.get(fault_type)
