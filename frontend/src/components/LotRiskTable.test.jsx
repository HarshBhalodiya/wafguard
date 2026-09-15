import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import LotRiskTable from "./LotRiskTable.jsx";

const LOTS = [
  { lot_id: "LOT_10901", product: "10NM_SOC", equipment: "ETCH_007", process_step: "ETCH", yield: 71.62, predicted_yield: 75.52, risk_level: "HIGH", risk_probability: 0.6157, process_start: "2026-09-06T16:41:07" },
  { lot_id: "LOT_10001", product: "5NM_CPU", equipment: "CVD_001", process_step: "CVD", yield: 98.2, predicted_yield: 97.9, risk_level: "LOW", risk_probability: 0.05, process_start: "2026-05-01T10:00:00" },
];

function renderWithRouter(ui) {
  return render(<MemoryRouter>{ui}</MemoryRouter>);
}

describe("LotRiskTable", () => {
  it("shows an empty state when there are no lots", () => {
    renderWithRouter(<LotRiskTable lots={[]} />);
    expect(screen.getByText(/no lots to show/i)).toBeInTheDocument();
  });

  it("renders one row per lot with a link to its detail page", () => {
    renderWithRouter(<LotRiskTable lots={LOTS} />);
    const link = screen.getByRole("link", { name: "LOT_10901" });
    expect(link).toHaveAttribute("href", "/lots/LOT_10901");
    expect(screen.getByText("LOT_10001")).toBeInTheDocument();
  });

  it("prefers predicted_yield over actual yield when both are present", () => {
    renderWithRouter(<LotRiskTable lots={LOTS} />);
    expect(screen.getByText("75.5%")).toBeInTheDocument();
  });

  it("hides equipment/step/date columns in compact mode", () => {
    renderWithRouter(<LotRiskTable lots={LOTS} compact />);
    expect(screen.queryByText("ETCH_007")).not.toBeInTheDocument();
  });
});
