import { useState } from "react";
import Plot from "react-plotly.js";
import { charts } from "../theme.js";
import { EmptyState } from "./LoadingState.jsx";

// Colours come from the shared palette; the stops themselves are semantic
// (pass / fail / attention) rather than decorative.
const { outside, pass, fail, attentionMid, attentionHigh } = charts.colorscale;
const DIE_COLORSCALE = [
  [0, outside],
  [0.49, outside],
  [0.5, pass],
  [0.99, pass],
  [1, fail],
];

const CAM_COLORSCALE = [
  [0, outside],
  [0.5, attentionMid],
  [1, attentionHigh],
];

export default function WaferMapViewer({ dieGrid, gradCam, height = 360 }) {
  const [showCam, setShowCam] = useState(false);

  if (!dieGrid) return <EmptyState label="No wafer map available." />;

  const z = showCam && gradCam ? gradCam : dieGrid;
  const colorscale = showCam && gradCam ? CAM_COLORSCALE : DIE_COLORSCALE;
  const zmax = showCam && gradCam ? 1 : 2;

  return (
    <div>
      {gradCam && (
        <div className="flex justify-end mb-2">
          <div className="inline-flex rounded-md border border-surface-border overflow-hidden text-xs">
            <button
              onClick={() => setShowCam(false)}
              className={`px-2.5 py-1 ${!showCam ? "bg-brand/15 text-brand" : "text-ink-secondary hover:text-ink-primary"}`}
            >
              Wafer Map
            </button>
            <button
              onClick={() => setShowCam(true)}
              className={`px-2.5 py-1 border-l border-surface-border ${showCam ? "bg-brand/15 text-brand" : "text-ink-secondary hover:text-ink-primary"}`}
            >
              Model Attention (Grad-CAM)
            </button>
          </div>
        </div>
      )}
      <Plot
        data={[
          {
            z,
            type: "heatmap",
            colorscale,
            zmin: 0,
            zmax,
            showscale: false,
            hoverongaps: false,
            hovertemplate: showCam ? "attention: %{z:.2f}<extra></extra>" : "%{z}<extra></extra>",
          },
        ]}
        layout={{
          autosize: true,
          height,
          margin: { l: 0, r: 0, t: 0, b: 0 },
          paper_bgcolor: "transparent",
          plot_bgcolor: "transparent",
          xaxis: { visible: false, scaleanchor: "y" },
          yaxis: { visible: false, autorange: "reversed" },
        }}
        config={{ displayModeBar: false, responsive: true }}
        style={{ width: "100%", height }}
        useResizeHandler
      />
      {!showCam && (
        <div className="flex items-center gap-4 mt-2 text-[11px] text-ink-muted">
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-status-healthy" />Pass</span>
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-status-critical" />Fail</span>
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-surface-border" />Outside wafer</span>
        </div>
      )}
    </div>
  );
}
