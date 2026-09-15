import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ModelPerformanceCard from "./ModelPerformanceCard.jsx";

describe("ModelPerformanceCard", () => {
  it("renders baseline vs production for a regression model", () => {
    render(
      <ModelPerformanceCard
        model={{
          model_name: "lot_yield_regressor",
          version: "1.0.0",
          task: "regression",
          dataset: "Synthetic lots (DEMO / SIMULATED DATA)",
          trained_at: "2026-09-14T08:22:00Z",
          metrics: { baseline: { mae: 3.324, rmse: 4.205, r2: 0.656 }, production: { mae: 3.601, rmse: 4.655, r2: 0.579 } },
        }}
      />
    );
    expect(screen.getByText("lot_yield_regressor")).toBeInTheDocument();
    expect(screen.getByText("3.324")).toBeInTheDocument();
    expect(screen.getByText("3.601")).toBeInTheDocument();
  });

  it("renders per-class precision/recall for a vision model", () => {
    render(
      <ModelPerformanceCard
        model={{
          model_name: "wafer_pattern_cnn",
          version: "1.0.0",
          task: "vision_classification",
          dataset: "Procedurally generated synthetic wafer maps (DEMO / SIMULATED DATA)",
          trained_at: "2026-09-14T08:30:00Z",
          metrics: {
            accuracy: 0.896,
            macro_f1: 0.894,
            per_class: {
              "Edge-Ring": { precision: 1.0, recall: 1.0 },
              Center: { precision: 0.696, recall: 0.867 },
            },
          },
        }}
      />
    );
    expect(screen.getByText("wafer_pattern_cnn")).toBeInTheDocument();
    expect(screen.getByText("0.896")).toBeInTheDocument();
    expect(screen.getByText("Edge-Ring")).toBeInTheDocument();
    expect(screen.getByText("Center")).toBeInTheDocument();
  });
});
