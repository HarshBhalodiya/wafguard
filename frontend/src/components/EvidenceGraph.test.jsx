import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import EvidenceGraph from "./EvidenceGraph.jsx";

const GRAPH = {
  graph_version: "1.0.0",
  rules_version: "1.0.0",
  lot_id: "LOT_10901",
  dominant_wafer_pattern: "Edge-Ring",
  nodes: [
    { id: "equipment:ETCH_007", type: "equipment", label: "ETCH_007", source: "measured", value: null, detail: "process step ETCH", evidence_ids: ["lot:LOT_10901:equipment"] },
    { id: "sensor_issue:rf-power", type: "sensor_issue", label: "RF Power", source: "measured", value: 0.71, detail: "HIGH confidence", evidence_ids: ["rc:LOT_10901:rf-power:0"] },
    { id: "mechanism:plasma_power_instability", type: "mechanism", label: "Plasma power delivery instability", source: "rule", value: null, detail: "rf_power_drift pushes rf_power", evidence_ids: ["rule:rf-power:plasma-power-instability"] },
    { id: "pattern:Edge-Ring", type: "defect_pattern", label: "Edge-Ring", source: "measured", value: 0.41, detail: "observed", evidence_ids: ["rc:LOT_10901:rf-power:2"] },
    { id: "yield_loss", type: "yield_loss", label: "Yield loss on LOT_10901", source: "measured", value: 71.62, detail: "actual lot yield 71.62%", evidence_ids: ["lot:LOT_10901:yield"] },
  ],
  edges: [
    { id: "e1", source: "equipment:ETCH_007", target: "sensor_issue:rf-power", strength: 0.6, kind: "measured", evidence_ids: ["rc:LOT_10901:rf-power:1"], label: "measured equipment association" },
    { id: "e2", source: "sensor_issue:rf-power", target: "mechanism:plasma_power_instability", strength: 0.57, kind: "rule", evidence_ids: ["rule:rf-power:plasma-power-instability"], label: "candidate score x rule strength" },
    { id: "e3", source: "mechanism:plasma_power_instability", target: "pattern:Edge-Ring", strength: 0.8, kind: "measured", evidence_ids: ["rc:LOT_10901:rf-power:2"], label: "corroborated" },
    { id: "e4", source: "pattern:Edge-Ring", target: "yield_loss", strength: 0.41, kind: "measured", evidence_ids: ["lot:LOT_10901:yield"], label: "measured defect density" },
  ],
  gaps: ["Voltage: no mechanism rule — no authored physical mechanism."],
  disclaimer: "Candidate links, not confirmed causes. Synthetic dataset.",
};

describe("EvidenceGraph", () => {
  it("shows an empty state when the lot has no graph", () => {
    render(<EvidenceGraph graph={null} />);
    expect(screen.getByText(/no evidence graph available/i)).toBeInTheDocument();
  });

  it("renders node labels for every column that is present", () => {
    render(<EvidenceGraph graph={GRAPH} />);
    expect(screen.getByTestId("evidence-graph")).toBeInTheDocument();
    expect(screen.getByText("ETCH_007")).toBeInTheDocument();
    expect(screen.getByText("RF Power")).toBeInTheDocument();
    expect(screen.getByText("Edge-Ring")).toBeInTheDocument();
    expect(screen.getByText("Yield loss on LOT_10901")).toBeInTheDocument();
  });

  it("encodes measured edges solid and rule edges dashed, with strength as thickness", () => {
    const { container } = render(<EvidenceGraph graph={GRAPH} />);
    const lines = [...container.querySelectorAll("line")].filter((line) => line.querySelector("title"));
    expect(lines).toHaveLength(4);
    const dashed = lines.filter((line) => line.getAttribute("stroke-dasharray"));
    expect(dashed).toHaveLength(1);
    expect(dashed[0].getAttribute("stroke-dasharray")).toBe("4 3");
    // strength 0.8 → 1 + 0.8*3 = 3.4
    const strong = lines.find((line) => line.querySelector("title").textContent.includes("strength 0.8"));
    expect(Number(strong.getAttribute("stroke-width"))).toBeCloseTo(3.4, 5);
  });

  it("surfaces the rules gaps and the candidate-not-cause disclaimer", () => {
    render(<EvidenceGraph graph={GRAPH} />);
    expect(screen.getByText(/Voltage: no mechanism rule/)).toBeInTheDocument();
    expect(screen.getByText(/not confirmed causes/i)).toBeInTheDocument();
  });
});
