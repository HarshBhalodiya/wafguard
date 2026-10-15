import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import Equipment from "./Equipment.jsx";
import { get } from "../services/api";

vi.mock("../services/api", () => ({
  get: vi.fn(),
}));

const EQUIPMENT = [
  {
    equipment_id: "ETCH_007",
    tool_family: "Etch",
    chamber: "C1",
    status: "warning",
    health_score: 62,
    anomaly_status: "drift suspected",
  },
];

const DETAIL = {
  ...EQUIPMENT[0],
  recent_maintenance: [],
  recent_lots: [],
  days_since_last_maintenance: null,
  peers: [{ equipment_id: "ETCH_008" }],
};

function chartWith(breaches) {
  return {
    equipment_id: "ETCH_007",
    parameter: "rf_power",
    n_history: 12,
    sigma_reliable: true,
    points: Array.from({ length: 12 }, (_, i) => ({ lot_id: `L${i}`, ts: "t", value: 1, z: null })),
    breaches: Array.from({ length: breaches }, (_, i) => ({ index: i, lot_id: `L${i}`, rule: "R1", detail: "beyond 3σ" })),
  };
}

function mockApi() {
  get.mockImplementation((url, params = {}) => {
    if (url === "/equipment") return Promise.resolve(EQUIPMENT);
    if (url === "/equipment/ETCH_007") return Promise.resolve(DETAIL);
    if (url === "/equipment/ETCH_007/control-chart") {
      // rf_power carries breaches in this fixture; the other parameters are clean
      return Promise.resolve(params.parameter === "rf_power" ? chartWith(2) : chartWith(0));
    }
    return Promise.reject(new Error(`unexpected ${url}`));
  });
}

describe("Equipment — control-chart breach context (Phase 8, P8-4)", () => {
  afterEach(() => vi.resetAllMocks());

  it("shows a lot-level SPC breach chip on the equipment card", async () => {
    mockApi();
    render(
      <MemoryRouter>
        <Equipment />
      </MemoryRouter>
    );

    await waitFor(() => expect(screen.getByText("2 breaches")).toBeInTheDocument());
    expect(screen.getByText(/SPC \(lot-level\)/)).toBeInTheDocument();
    expect(screen.getByText(/last 40 lots/)).toBeInTheDocument();
    // the generator's static health score is rendered unchanged — the SPC chip
    // adds context next to it, it does not reinterpret it
    expect(screen.getByText("62/100")).toBeInTheDocument();
  });

  it("opens a per-parameter breach summary labeled lot-level", async () => {
    mockApi();
    render(
      <MemoryRouter>
        <Equipment />
      </MemoryRouter>
    );

    await waitFor(() => expect(screen.getByText("ETCH_007")).toBeInTheDocument());
    fireEvent.click(screen.getByText("ETCH_007"));

    await waitFor(() => expect(screen.getByText("Control-Chart Context")).toBeInTheDocument());
    expect(screen.getByText(/not per-reading SPC/)).toBeInTheDocument();
    expect(screen.getByText("RF Power")).toBeInTheDocument();
    expect(screen.getByText("Temperature")).toBeInTheDocument();
    // card chip (2) + detail RF Power tile (2); other parameters report 0
    expect(screen.getAllByText(/breaches/).length).toBeGreaterThanOrEqual(2);
  });

  it("keeps rendering the equipment list when a control chart fails to load", async () => {
    get.mockImplementation((url) => {
      if (url === "/equipment") return Promise.resolve(EQUIPMENT);
      if (url.endsWith("/control-chart")) return Promise.reject(new Error("boom"));
      return Promise.reject(new Error(`unexpected ${url}`));
    });
    render(
      <MemoryRouter>
        <Equipment />
      </MemoryRouter>
    );

    await waitFor(() => expect(screen.getByText("ETCH_007")).toBeInTheDocument());
    expect(screen.queryByText(/SPC \(lot-level\)/)).not.toBeInTheDocument();
  });
});
