import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import EvidenceChain from "./EvidenceChain.jsx";

describe("EvidenceChain", () => {
  it("shows an empty state with no steps", () => {
    render(<EvidenceChain steps={[]} />);
    expect(screen.getByText(/no evidence chain/i)).toBeInTheDocument();
  });

  it("renders each step's kind tag, label, and source", () => {
    render(
      <EvidenceChain
        steps={[
          { kind: "FACT", label: "RF Power reading deviates from normal.", source: "process_runs" },
          { kind: "MODEL_OUTPUT", label: "Root-cause candidate: RF Power (HIGH confidence).", source: "rootcause_multi_signal_v1" },
          { kind: "RECOMMENDATION", label: "Inspect the RF generator on ETCH_007." },
        ]}
      />
    );
    expect(screen.getByText("FACT")).toBeInTheDocument();
    expect(screen.getByText("MODEL OUTPUT")).toBeInTheDocument();
    expect(screen.getByText("RECOMMENDATION")).toBeInTheDocument();
    expect(screen.getByText("RF Power reading deviates from normal.")).toBeInTheDocument();
    expect(screen.getByText("process_runs")).toBeInTheDocument();
  });
});
