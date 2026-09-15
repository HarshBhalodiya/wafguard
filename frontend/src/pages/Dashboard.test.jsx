import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import Dashboard from "./Dashboard.jsx";
import { get } from "../services/api";

vi.mock("../services/api", () => ({
  get: vi.fn(),
}));

const DASHBOARD_PAYLOAD = {
  overall_yield: 93.4,
  avg_predicted_yield: 93.45,
  at_risk_lot_count: 83,
  high_risk_lot_count: 10,
  defect_rate: 0.1288,
  equipment_warnings: 1,
  top_root_causes: [{ candidate: "RF Power", share: 0.6982 }],
  yield_trend: [{ period: "2026-W15", value: 91.4, n_lots: 23 }],
  risk_trend: [{ period: "2026-W15", value: 0.14, n_lots: 23 }],
  recent_alerts: [{ lot_id: "LOT_10901", severity: "HIGH", created_at: "2026-09-14T14:39:16", message: "Lot LOT_10901 on ETCH_007 is at 62% predicted low-yield risk.", acknowledged_by: null, resolution: null }],
  top_risk_lots: [
    { lot_id: "LOT_10901", product: "10NM_SOC", equipment: "ETCH_007", process_step: "ETCH", yield: 71.62, predicted_yield: 75.52, risk_level: "HIGH", risk_probability: 0.6157, process_start: "2026-09-06T16:41:07" },
  ],
  total_lots: 950,
};

function renderDashboard() {
  return render(
    <MemoryRouter>
      <Dashboard />
    </MemoryRouter>
  );
}

describe("Dashboard", () => {
  afterEach(() => vi.resetAllMocks());

  it("shows a loading state before data arrives", () => {
    get.mockReturnValue(new Promise(() => {})); // never resolves
    renderDashboard();
    expect(screen.getByText(/loading dashboard/i)).toBeInTheDocument();
  });

  it("renders key metrics once data loads", async () => {
    get.mockResolvedValue(DASHBOARD_PAYLOAD);
    renderDashboard();

    await waitFor(() => expect(screen.getByText("93.4%")).toBeInTheDocument());
    expect(screen.getByText("83")).toBeInTheDocument();
    expect(screen.getByText("10 high risk")).toBeInTheDocument();
    expect(screen.getByText("RF Power")).toBeInTheDocument();
    // LOT_10901 legitimately appears twice (the at-risk table and the alert panel).
    expect(screen.getAllByText("LOT_10901").length).toBeGreaterThanOrEqual(2);
  });

  it("shows an error state with a retry button when the API call fails", async () => {
    get.mockRejectedValue(Object.assign(new Error("Database unavailable"), { code: "DATABASE_UNAVAILABLE" }));
    renderDashboard();

    await waitFor(() => expect(screen.getByText("Database unavailable")).toBeInTheDocument());
    expect(screen.getByText("DATABASE_UNAVAILABLE")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });
});
