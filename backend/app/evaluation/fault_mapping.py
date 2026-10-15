"""Ground-truth fault → acceptable root-cause candidates.

Used ONLY by evaluation code (the pattern-table builder and the ranking
harness). Each mapping is justified by the generator's own physics — the
same `process_physics.FAULT_SEVERITY` / `SENSOR_DRIFT_BIAS` / `mechanisms`
definitions that produced the data — never by what makes the engine look
good. Faults with no defensible mapping to the engine's 10-candidate set
are declared unmappable *here* and reported explicitly by the harness
instead of being quietly folded into the metrics.
"""

# Candidates a diagnosis may rank for a given true fault and still count as
# a hit. Fractional credit (1 / len(set)) is used everywhere a fault maps to
# several candidates so multi-mapped faults cannot inflate any one candidate.
FAULT_TO_CANDIDATES: dict[str, frozenset[str]] = {
    # FAULT_SEVERITY: rf_power_drift pushes only the rf_power channel.
    "rf_power_drift": frozenset({"RF Power"}),
    # FAULT_SEVERITY: chamber_pressure_instability pushes only pressure.
    "chamber_pressure_instability": frozenset({"Pressure"}),
    # FAULT_SEVERITY: recipe transitions offset gas_flow first, pressure second.
    "recipe_transition_problem": frozenset({"Gas Flow", "Pressure"}),
    # SENSOR_DRIFT_BIAS biases EVERY channel, so the dominant visible signature
    # can be any of the four tracked sensor channels — all are acceptable.
    "sensor_drift": frozenset({"Temperature", "RF Power", "Pressure", "Gas Flow"}),
    # Degradation is physically a vibration/tool-condition fault; vibration is
    # not one of the 10 candidates, so the defensible candidates are the
    # tool-condition ones the engine actually offers.
    "equipment_degradation": frozenset(
        {"Maintenance Overdue", "Equipment Failure History", "Equipment Yield History"}
    ),
    # No defensible mapping in the current candidate set — reported as
    # unmappable by the harness, never scored leniently:
    "sensor_dropout": frozenset(),
    "contamination_event": frozenset(),
    "none": frozenset(),
}

# Faults the harness must count and report separately (not score).
UNMAPPABLE_FAULTS: frozenset[str] = frozenset({"sensor_dropout", "contamination_event"})


def acceptable_candidates(true_fault: str) -> frozenset[str]:
    return FAULT_TO_CANDIDATES.get(true_fault, frozenset())
