"""
Process physics: windows, sensor baselines, and noise.

These are the documented "normal operating windows" for a generic 300mm
plasma etch step. They are SYNTHETIC reference values (labeled as such) —
chosen to be physically plausible, not to match any real tool or recipe.
`app.config.PROCESS_PARAMETER_RANGES` (used by the risk engine and the v1
generator) re-exports the four overlapping channels from here so there is
exactly one definition of "normal" in the codebase.
"""
from dataclasses import dataclass

@dataclass(frozen=True)
class ChannelSpec:
    name: str
    unit: str
    center: float
    half_width: float   # ± around center = the "in-spec" band
    noise_sigma: float  # 1-sigma point-to-point sensor noise

    @property
    def lo(self) -> float:
        return self.center - self.half_width

    @property
    def hi(self) -> float:
        return self.center + self.half_width


CHANNEL_SPECS: dict = {
    "pressure":         ChannelSpec("pressure",         "Torr",  2.75,  0.35,  0.02),
    "temperature":      ChannelSpec("temperature",      "degC",  437.5, 7.5,   0.8),
    "rf_power":         ChannelSpec("rf_power",         "W",     857.5, 17.5,  2.5),
    "gas_flow":         ChannelSpec("gas_flow",         "sccm",  51.5,  6.5,   0.6),
    "vibration":        ChannelSpec("vibration",        "mm/s",  1.50,  0.40,  0.08),
    "plasma_intensity": ChannelSpec("plasma_intensity", "a.u.",  100.0, 10.0,  2.0),
}

# Relative severity multipliers applied to a channel's half-width when a
# fault pushes it out of spec. 1.0 = exactly to the band edge.
FAULT_SEVERITY = {
    "chamber_pressure_instability": {"pressure": (0.9, 1.8)},
    "rf_power_drift":               {"rf_power": (0.9, 1.6)},
    "sensor_drift":                 {},  # bias magnitudes are channel-specific, see SENSOR_DRIFT_BIAS
    "equipment_degradation":        {"vibration": (0.7, 1.5)},
    "sensor_dropout":               {},  # dropouts corrupt samples, don't shift the mean
    "recipe_transition_problem":    {"gas_flow": (0.8, 1.4), "pressure": (0.4, 0.9)},
    "contamination_event":          {},  # acts through defects, not sensor channels
}

# Slow sensor bias: fraction of half-width added across the whole run.
# Deliberately LARGE enough to carry the run out of its spec band by the
# end (the ramp is linear in time, so ~1.2-1.8 half-widths at the final
# sample). A drift that stays inside spec (the original 0.35-0.60) is
# undetectable from telemetry and cannot honestly explain a 7pp yield
# loss - which made sensor-drift lots unlearnable and unrisk-scoreable.
SENSOR_DRIFT_BIAS = {
    "pressure": 1.5,
    "temperature": 1.6,
    "rf_power": 1.4,
    "gas_flow": 1.5,
    "vibration": 1.3,
    "plasma_intensity": 1.2,
}

# Recipe transition: first n seconds run at an offset recipe before recovering.
RECOVER_TIME_SECONDS = 120

# Equipment degradation: vibration grows with days since last maintenance.
DEGRADATION_DAYS_FULL = 120.0      # days since PM at which degradation saturates
DEGRADATION_MAX_EXCESS = 1.1       # max extra half-widths added at saturation

# Multiplier applied to an equipment's baseline risk of drawing a fault,
# growing with days since its last maintenance event.
DEGRADATION_RISK_GROWTH = 0.20     # +20% fault odds at DEGRADATION_DAYS_FULL

# Excursions add random one-off spikes to a channel on some normal runs.
EXCURSION_RATE_ON_NORMAL = 0.06
EXCURSION_MAGNITUDE = (1.1, 1.8)   # in half-widths, signed at random

# Ambient noise on final yield (percentage points) and unexplained defect rate.
YIELD_NOISE_SIGMA = 0.9
BACKGROUND_DEFECT_PROB = 0.012
