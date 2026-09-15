import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import RootCauseChart from "./RootCauseChart.jsx";

describe("RootCauseChart", () => {
  it("shows an empty state with no candidates", () => {
    render(<RootCauseChart candidates={[]} />);
    expect(screen.getByText(/no root-cause candidates/i)).toBeInTheDocument();
  });

  it("renders each candidate's name and rounded percentage", () => {
    render(
      <RootCauseChart
        candidates={[
          { candidate: "RF Power", score: 0.745 },
          { candidate: "Temperature", score: 0.498 },
        ]}
      />
    );
    expect(screen.getByText("RF Power")).toBeInTheDocument();
    expect(screen.getByText("75%")).toBeInTheDocument();
    expect(screen.getByText("Temperature")).toBeInTheDocument();
    expect(screen.getByText("50%")).toBeInTheDocument();
  });
});
