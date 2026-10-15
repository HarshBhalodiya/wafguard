import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import ControlCharts from "./ControlCharts.jsx";
import { apiRequest } from "../services/api";

vi.mock("../services/api", () => ({
  apiRequest: vi.fn(),
}));

const EQUIPMENT = [{ equipment_id: "ETCH_007" }, { equipment_id: "CMP_002" }];

const CHART = {
  equipment_id: "ETCH_007",
  parameter: "rf_power",
  granularity: "lot-level telemetry means (not per-reading SPC)",
  n_history: 10,
  sigma_reliable: true,
  centerline: 858.1,
  sigma: 4.2,
  ucl: 870.7,
  lcl: 845.5,
  rules: ["R1: beyond 3σ"],
  points: [
    { lot_id: "LOT_1", ts: "t1", value: 857.0, z: null },
    { lot_id: "LOT_2", ts: "t2", value: 880.0, z: 5.2 },
  ],
  breaches: [{ index: 1, lot_id: "LOT_2", rule: "R1", detail: "beyond 3σ (z=+5.20)" }],
  source: "prediction feature snapshots",
};

function mockApi() {
  apiRequest.mockImplementation(({ url }) => {
    if (url === "/equipment") return Promise.resolve(EQUIPMENT);
    if (url === "/equipment/ETCH_007/control-chart") return Promise.resolve(CHART);
    return Promise.reject(new Error(`unexpected ${url}`));
  });
}

describe("ControlCharts", () => {
  afterEach(() => vi.resetAllMocks());

  it("renders the chart meta and breach list after picking equipment", async () => {
    mockApi();
    render(
      <MemoryRouter>
        <ControlCharts />
      </MemoryRouter>
    );

    // equipment list loads, then the user picks one
    await waitFor(() => expect(screen.getByText("ETCH_007")).toBeTruthy());
    const select = screen.getAllByRole("combobox")[0];
    select.value = "ETCH_007";
    select.dispatchEvent(new Event("change", { bubbles: true }));

    await waitFor(() => expect(screen.getByText(/LOT_2: R1/)).toBeTruthy());
    expect(screen.getByText(/centerline/)).toBeTruthy();
    expect(screen.getByText(/Lot-level SPC/)).toBeTruthy();
  });

  it("shows a prompt before an equipment is selected", () => {
    mockApi();
    render(
      <MemoryRouter>
        <ControlCharts />
      </MemoryRouter>
    );
    expect(screen.getByText(/Pick an equipment and parameter/)).toBeInTheDocument();
  });
});
