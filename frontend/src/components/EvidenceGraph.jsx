import { EmptyState } from "./LoadingState.jsx";

/**
 * Root-cause evidence graph (brief PHASE 3C).
 *
 * Renders the backend's nodes/edges as a left-to-right layered diagram:
 * equipment/chamber → sensor issue → mechanism → defect pattern → yield loss.
 *
 * The visual encoding is the point: solid edges are `measured` (this lot's own
 * data), dashed edges are `rule` (the readable rules file), and line thickness is
 * edge strength. Every edge's justification is available on hover.
 *
 * No graph library is pulled in — the layout is a small deterministic pass over
 * the node list, so this adds no dependency to the bundle.
 */
const COLUMN_OF = {
  equipment: 0,
  chamber: 0,
  sensor_issue: 1,
  mechanism: 2,
  defect_pattern: 3,
  yield_loss: 4,
};

const COLUMN_LABELS = ["Tool / chamber", "Sensor issue", "Mechanism", "Defect pattern", "Yield loss"];
const NODE_W = 168;
const NODE_H = 48;
const COL_W = 210;
const ROW_H = 66;
const PAD = 16;

function layout(nodes, edges) {
  const byColumn = new Map();
  for (const node of nodes) {
    const col = COLUMN_OF[node.type] ?? 0;
    if (!byColumn.has(col)) byColumn.set(col, []);
    byColumn.get(col).push(node);
  }

  const placed = new Map();
  const columns = [...byColumn.keys()].sort((a, b) => a - b);
  for (const col of columns) {
    byColumn.get(col).forEach((node, index) => {
      let y = PAD + index * ROW_H;
      if (col > 0) {
        // Flow left-to-right: sit below the average of the node's upstream
        // neighbours when they are already placed, so edges do not criss-cross
        // the whole canvas. Columns are visited in order, so sources come first.
        const upstreamY = edges
          .filter((edge) => edge.target === node.id)
          .map((edge) => placed.get(edge.source))
          .filter((value) => value !== undefined);
        if (upstreamY.length > 0) {
          const mean = upstreamY.reduce((sum, value) => sum + value, 0) / upstreamY.length;
          y = Math.max(PAD, mean - NODE_H / 2);
        }
      }
      placed.set(node.id, y);
    });
  }

  const height = Math.max(...[...placed.values()].map((y) => y + NODE_H + PAD), PAD + NODE_H);
  return { placed, height };
}

export default function EvidenceGraph({ graph }) {
  if (!graph || !graph.nodes || graph.nodes.length === 0) {
    return <EmptyState label="No evidence graph available for this lot yet." />;
  }
  const nodes = graph.nodes;
  const edges = graph.edges || [];
  const { placed, height } = layout(nodes, edges);
  const width = PAD + 4 * COL_W + NODE_W + PAD;
  const positionOf = (id) => {
    const node = nodes.find((candidate) => candidate.id === id);
    if (!node) return null;
    const col = COLUMN_OF[node.type] ?? 0;
    const x = PAD + col * COL_W;
    return { x, y: placed.get(id) ?? PAD };
  };

  return (
    <div data-testid="evidence-graph" className="space-y-3">
      <div className="flex flex-wrap gap-4 text-[11px] text-ink-muted">
        <span className="inline-flex items-center gap-1.5">
          <svg width="22" height="6" aria-hidden="true">
            <line x1="0" y1="3" x2="22" y2="3" className="stroke-brand" strokeWidth="2" />
          </svg>
          measured (this lot&apos;s data)
        </span>
        <span className="inline-flex items-center gap-1.5">
          <svg width="22" height="6" aria-hidden="true">
            <line x1="0" y1="3" x2="22" y2="3" className="stroke-ink-muted" strokeWidth="2" strokeDasharray="4 3" />
          </svg>
          rule (readable rules file)
        </span>
        <span>line thickness = edge strength</span>
      </div>

      <div className="overflow-x-auto">
        <svg viewBox={`0 0 ${width} ${height}`} className="min-w-full" role="img" aria-label="Root-cause evidence graph">
          {edges.map((edge) => {
            const from = positionOf(edge.source);
            const to = positionOf(edge.target);
            if (!from || !to) return null;
            return (
              <line
                key={edge.id}
                x1={from.x + NODE_W}
                y1={from.y + NODE_H / 2}
                x2={to.x}
                y2={to.y + NODE_H / 2}
                className={edge.kind === "measured" ? "stroke-brand" : "stroke-ink-muted"}
                strokeWidth={1 + edge.strength * 3}
                strokeOpacity={0.35 + edge.strength * 0.5}
                strokeDasharray={edge.kind === "measured" ? undefined : "4 3"}
              >
                <title>{`${edge.label} — strength ${edge.strength} (${edge.kind})`}</title>
              </line>
            );
          })}

          {COLUMN_LABELS.map((label, index) => (
            <text
              key={label}
              x={PAD + index * COL_W}
              y={12}
              className="fill-ink-muted text-[10px] uppercase"
            >
              {label}
            </text>
          ))}

          {nodes.map((node) => {
            const position = positionOf(node.id);
            return (
              <g key={node.id} data-testid={`graph-node-${node.id}`}>
                <rect
                  x={position.x}
                  y={position.y}
                  width={NODE_W}
                  height={NODE_H}
                  rx="6"
                  className="fill-surface-raised stroke-surface-border"
                  strokeWidth="1"
                />
                <text x={position.x + 10} y={position.y + 19} className="fill-ink-primary text-[11px] font-medium">
                  {node.label.length > 24 ? `${node.label.slice(0, 23)}…` : node.label}
                </text>
                <text x={position.x + 10} y={position.y + 34} className="fill-ink-muted text-[9px]">
                  {node.source}
                  {node.value !== null && node.value !== undefined ? ` · ${node.value}` : ""}
                </text>
              </g>
            );
          })}
        </svg>
      </div>

      {graph.gaps && graph.gaps.length > 0 && (
        <ul className="text-[11px] text-ink-muted space-y-1">
          {graph.gaps.map((gap) => (
            <li key={gap}>• {gap}</li>
          ))}
        </ul>
      )}

      <p className="text-[11px] text-ink-muted">{graph.disclaimer}</p>
    </div>
  );
}
