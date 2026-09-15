import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import RiskBadge from "./RiskBadge.jsx";

describe("RiskBadge", () => {
  it("renders the risk level", () => {
    render(<RiskBadge level="HIGH" />);
    expect(screen.getByText("HIGH")).toBeInTheDocument();
  });

  it("renders the probability as a rounded percentage when given", () => {
    render(<RiskBadge level="MEDIUM" probability={0.6157} />);
    expect(screen.getByText("62%")).toBeInTheDocument();
  });

  it("omits the probability when not given", () => {
    render(<RiskBadge level="LOW" />);
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
  });

  it("falls back to an em dash for an unknown level", () => {
    render(<RiskBadge level={undefined} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});
