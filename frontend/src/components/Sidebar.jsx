import { NavLink } from "react-router-dom";

const NAV_SECTIONS = [
  {
    label: "Decisions",
    items: [
      { to: "/upcoming", label: "Upcoming Lots", icon: "alert" },
      { to: "/investigate", label: "Lot Investigation", icon: "search" },
      { to: "/root-cause-explorer", label: "Root Cause Explorer", icon: "search" },
      { to: "/health", label: "Model & Data Health", icon: "bar" },
    ],
  },
  {
    label: "Overview",
    items: [
      { to: "/dashboard", label: "Dashboard", icon: "grid" },
      { to: "/risk-monitor", label: "Risk Monitor", icon: "alert" },
      { to: "/alerts", label: "Alerts", icon: "bell" },
    ],
  },
  {
    label: "Investigation",
    items: [
      { to: "/lots", label: "Lots", icon: "layers" },
      { to: "/root-cause", label: "Root Cause", icon: "search" },
      { to: "/recommendations", label: "Recommendations", icon: "clipboard" },
      { to: "/similar-lots", label: "Similar Lots", icon: "copy" },
      { to: "/what-if", label: "What-If Simulator", icon: "sliders" },
    ],
  },
  {
    label: "Vision & Equipment",
    items: [
      { to: "/wafer-map", label: "Wafer Map", icon: "circle" },
      { to: "/defect-analyzer", label: "Defect Analyzer", icon: "camera" },
      { to: "/equipment", label: "Equipment", icon: "cpu" },
      { to: "/control-charts", label: "Control Charts", icon: "trend" },
    ],
  },
  {
    label: "Analytics",
    items: [
      { to: "/yield-analytics", label: "Yield Analytics", icon: "trend" },
      { to: "/model-performance", label: "Model Performance", icon: "bar" },
    ],
  },
  {
    label: "System",
    items: [{ to: "/settings", label: "Settings", icon: "gear" }],
  },
];

function Dot() {
  return <span className="w-1.5 h-1.5 rounded-full bg-current opacity-70" />;
}

export default function Sidebar() {
  return (
    <aside className="hidden md:flex md:flex-col w-64 shrink-0 h-screen sticky top-0 bg-surface-panel border-r border-surface-border">
      <div className="px-5 py-5 border-b border-surface-border">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-md bg-brand/15 border border-brand/30 flex items-center justify-center text-brand font-mono font-bold text-sm">
            W
          </div>
          <div>
            <div className="font-semibold text-ink-primary leading-tight">WafGuard AI</div>
            <div className="text-[11px] text-ink-muted leading-tight">Semiconductor Yield Intelligence</div>
          </div>
        </div>
      </div>
      <nav className="flex-1 overflow-y-auto px-3 py-4 space-y-6">
        {NAV_SECTIONS.map((section) => (
          <div key={section.label}>
            <div className="px-2 mb-2 label-caps">{section.label}</div>
            <div className="space-y-0.5">
              {section.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  className={({ isActive }) =>
                    `flex items-center gap-2.5 px-2.5 py-2 rounded-md text-sm font-medium transition-colors ${
                      isActive
                        ? "bg-brand/10 text-brand"
                        : "text-ink-secondary hover:text-ink-primary hover:bg-surface-raised"
                    }`
                  }
                >
                  <Dot />
                  {item.label}
                </NavLink>
              ))}
            </div>
          </div>
        ))}
      </nav>
      <div className="px-4 py-3 border-t border-surface-border text-[11px] text-ink-muted">
        Prototype — public/synthetic data. Not production-certified.
      </div>
    </aside>
  );
}
