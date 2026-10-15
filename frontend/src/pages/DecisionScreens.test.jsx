import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import UpcomingLots from "./UpcomingLots.jsx";
import ModelDataHealth from "./ModelDataHealth.jsx";
import LotInvestigation, { WHATIF_LABEL } from "./LotInvestigation.jsx";
import RootCauseExplorer from "./RootCauseExplorer.jsx";
import { get, post } from "../services/api";

vi.mock("../services/api", () => ({ get: vi.fn(), post: vi.fn() }));

function renderAt(ui, path = "/") {
  return render(<MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>);
}

const UPCOMING = {
  planned_start: "2026-09-13T22:00:00+00:00",
  label: "Pre-run model estimate — not a guarantee.",
  queue_note: "Derived queue: it is not a production dispatch list.",
  n_tools_scored: 2,
  n_tools_skipped: 1,
  lots: [
    { equipment_id: "ETCH_007", recipe_id: "REC_003", process_step: "ETCH", risk_level: "HIGH", risk_probability: 0.71, probability_low_yield: 0.62, predicted_yield: 78.1, main_reason: "model probability (0.62)" },
    { equipment_id: "CVD_002", recipe_id: "REC_010", process_step: "CVD", risk_level: "LOW", risk_probability: 0.12, probability_low_yield: 0.04, predicted_yield: 96.4, main_reason: "equipment risk (0.10)" },
  ],
};

const LOT = { lot_id: "LOT_11532", risk_level: "HIGH", risk_probability: 0.7, predicted_yield: 77.2, yield: 74.0, equipment: "ETCH_007", process_step: "ETCH", process_start: "2026-09-01T10:00:00Z" };
const ROOT_CAUSE = {
  candidates: [
    { candidate: "RF Power", score: 0.71, confidence: "HIGH", evidence: [{ evidence_id: "rc:LOT_11532:rf_power:0", label: "RF reading deviates 140% of the way to the band edge.", source: "process_runs" }] },
    { candidate: "Pressure", score: 0.42, confidence: "MEDIUM", evidence: [{ evidence_id: "rc:LOT_11532:pressure:0", label: "Pressure excursion for 12% of the run.", source: "sensor_data" }] },
  ],
};
const RECS = {
  label: "SYNTHETIC intervention history.",
  actions: [
    { action_id: "rf_generator_recalibration", text: "Recalibrate the RF generator on ETCH_007.", cause: "RF Power", recovery_text: "9/12", avg_yield_change: 11.4, small_sample_warning: null },
    { action_id: "throttle_valve_service", text: "Service the throttle valve on ETCH_007.", cause: "Pressure", recovery_text: "3/4", avg_yield_change: 6.0, small_sample_warning: "Small sample: only 4 recorded case(s) (< 10); treat the rate as weak evidence." },
  ],
};
const LIMITS = { label: WHATIF_LABEL, parameters: { rf_power: { unit: "W", recipe_window: [840, 875], physical: [805, 910] } } };

function routeLotApis(url) {
  if (url === "/lots/LOT_11532") return Promise.resolve(LOT);
  if (url === "/root-cause/LOT_11532") return Promise.resolve(ROOT_CAUSE);
  if (url === "/defects/lots/LOT_11532") return Promise.resolve({ inspection_pattern: "Edge-Ring", defect_density: 0.081, n_failed_wafers: 9, n_wafers: 25, by_type: { Particle: 40 } });
  if (url === "/lots/LOT_11532/timeline") return Promise.resolve({ events: [{ event_type: "PARAMETER_EXCURSION", ts: "2026-09-01T10:03:00Z", payload: { parameter: "rf_power" } }] });
  if (url === "/recommendations/LOT_11532") return Promise.resolve(RECS);
  if (url === "/whatif/limits") return Promise.resolve(LIMITS);
  if (url === "/similar/LOT_11532") return Promise.resolve({ insight: "2 of the 3 most similar historical lots experienced low yield.", similar_lots: [{ lot_id: "LOT_10400", equipment: "ETCH_007", yield: 72.0 }] });
  if (url === "/root-cause/LOT_11532/graph") return Promise.resolve({ nodes: [], edges: [] });
  return Promise.reject(Object.assign(new Error(`unexpected ${url}`), { code: "TEST" }));
}

afterEach(() => vi.resetAllMocks());

describe("UpcomingLots", () => {
  it("lists the derived queue in server (risk) order with reasons and the honesty labels", async () => {
    get.mockResolvedValue(UPCOMING);
    renderAt(<UpcomingLots />);
    await waitFor(() => expect(screen.getAllByTestId("upcoming-row")).toHaveLength(2));
    const rows = screen.getAllByTestId("upcoming-row");
    expect(rows[0]).toHaveTextContent("ETCH_007");
    expect(rows[0]).toHaveTextContent("model probability (0.62)");
    expect(rows[1]).toHaveTextContent("CVD_002");
    expect(screen.getByText(/not a production dispatch list/i)).toBeInTheDocument();
    expect(get).toHaveBeenCalledWith("/lot-risk/upcoming", { limit: 50 });
  });

  it("shows an error state with retry", async () => {
    get.mockRejectedValue(Object.assign(new Error("Model unavailable"), { code: "MODEL_UNAVAILABLE" }));
    renderAt(<UpcomingLots />);
    await waitFor(() => expect(screen.getByText("Model unavailable")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });
});

describe("ModelDataHealth", () => {
  it("flags drifted features and headlines monitoring totals", async () => {
    get.mockImplementation((url) => {
      if (url === "/quality/drift")
        return Promise.resolve({
          overall_status: "drift",
          windows: { reference: { n: 3500 }, current: { n: 480 } },
          features: [
            { feature: "rf_power_mean", group: "sensors", psi: 0.41, ks_p_value: 0.0001, psi_band: "significant", drifted: true },
            { feature: "pressure_mean", group: "sensors", psi: 0.02, ks_p_value: 0.4, psi_band: "stable", drifted: false },
          ],
        });
      if (url === "/quality/monitoring")
        return Promise.resolve({
          label: "Back-filled daily snapshots over SYNTHETIC historical data.",
          summary: { n_days: 2, first_day: "2026-09-01", last_day: "2026-09-02", n_predicted: 50, total_false_alarms: 3, total_missed_low_yield: 4, false_alarm_rate_of_flags: 0.2, miss_rate_of_low_yield: 0.4, latest_ece: 0.05 },
          snapshots: [
            { date: "2026-09-01", prediction_distribution: { mean_probability_low_yield: 0.1 }, calibration: { ece: 0.05 } },
            { date: "2026-09-02", prediction_distribution: { mean_probability_low_yield: 0.2 }, calibration: { ece: 0.06 } },
          ],
        });
      if (url === "/quality/summary") return Promise.resolve({ n_lots_reported: 5000, status_counts: { PASS: 4900, WARN: 100 }, issues_per_check: { stuck_values: 100 } });
      return Promise.reject(new Error(url));
    });
    renderAt(<ModelDataHealth />);
    await waitFor(() => expect(screen.getAllByTestId("drift-row")).toHaveLength(2));
    expect(screen.getByText("Drift: drift")).toBeInTheDocument();
    expect(screen.getByText("DRIFT")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("Missed low-yield lots")).toBeInTheDocument());
    expect(screen.getByText("4")).toBeInTheDocument();
    expect(screen.getByText("stuck_values")).toBeInTheDocument();
  });
});

describe("LotInvestigation", () => {
  it("walks risk → why → what happened → actions, with small-sample warnings", async () => {
    get.mockImplementation(routeLotApis);
    renderAt(<LotInvestigation />, "/investigate?lot_id=LOT_11532");
    await waitFor(() => expect(screen.getByText("Recalibrate the RF generator on ETCH_007.")).toBeInTheDocument());
    expect(screen.getByText(/1\. Risk/)).toBeInTheDocument();
    expect(screen.getAllByText("RF Power").length).toBeGreaterThan(0);
    expect(screen.getByText("Edge-Ring")).toBeInTheDocument();
    expect(screen.getByText("9/12")).toBeInTheDocument();
    expect(screen.getByText(/Small sample/)).toBeInTheDocument();
  });

  it("forces the counterfactual label and shows a range, and surfaces blocked changes", async () => {
    get.mockImplementation(routeLotApis);
    post.mockResolvedValueOnce({
      yield_before: { p10: 74.1, p50: 76.0, p90: 77.8, interval_80: [70.2, 81.4] },
      yield_after: { p10: 80.3, p50: 82.5, p90: 84.0, interval_80: [76.7, 87.9] },
      yield_difference: { p10: 4.9, p50: 6.5, p90: 7.6, members_agreeing_on_direction: 1 },
      risk_level: { before: "HIGH", after: "MEDIUM" },
      uncertainty_method: { measured_test_coverage_of_interval_80: 0.79 },
      warnings: [],
    });
    renderAt(<LotInvestigation />, "/investigate?lot_id=LOT_11532");
    await waitFor(() => expect(screen.getByLabelText("value")).toBeInTheDocument());
    expect(screen.getAllByText(WHATIF_LABEL).length).toBeGreaterThan(0);

    fireEvent.change(screen.getByLabelText("value"), { target: { value: "857.5" } });
    fireEvent.click(screen.getByRole("button", { name: /run counterfactual/i }));
    await waitFor(() => expect(screen.getByTestId("range-Yield after")).toBeInTheDocument());
    expect(post).toHaveBeenCalledWith("/whatif", { lot_id: "LOT_11532", changes: { rf_power: 857.5 } });
    expect(screen.getByText(/P10 4.9 – P90 7.6/)).toBeInTheDocument();

    post.mockRejectedValueOnce(Object.assign(new Error("Change blocked: rf_power=2000 W is outside the physical limit [805, 910]"), { code: "VALIDATION_ERROR" }));
    fireEvent.change(screen.getByLabelText("value"), { target: { value: "2000" } });
    fireEvent.click(screen.getByRole("button", { name: /run counterfactual/i }));
    await waitFor(() => expect(screen.getByText(/outside the physical limit/)).toBeInTheDocument());
  });
});

describe("RootCauseExplorer", () => {
  it("filters evidence and actions to the selected cause", async () => {
    get.mockImplementation(routeLotApis);
    renderAt(<RootCauseExplorer />, "/root-cause-explorer?lot_id=LOT_11532");
    await waitFor(() => expect(screen.getByTestId("evidence-list")).toBeInTheDocument());
    expect(screen.getByText(/RF reading deviates/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("Recalibrate the RF generator on ETCH_007.")).toBeInTheDocument());
    expect(screen.queryByText("Service the throttle valve on ETCH_007.")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Pressure/ }));
    expect(screen.getByText(/Pressure excursion for 12%/)).toBeInTheDocument();
    expect(screen.getByText("Service the throttle valve on ETCH_007.")).toBeInTheDocument();
    expect(screen.getByText(/2 of the 3 most similar/)).toBeInTheDocument();
  });
});
