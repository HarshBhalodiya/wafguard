/**
 * Design-constraint tests.
 *
 * These encode review feedback about the UI's look so it cannot quietly come
 * back: no gradient fills in charts, no pill-shaped measurement bars, and
 * colours taken from the shared palette rather than hard-coded per component.
 *
 * jsdom does no layout, so `ResponsiveContainer` would measure 0x0 and render
 * nothing — which would make every assertion here pass vacuously. The layout
 * mocks below give it a real size so the SVG is actually produced.
 */
import { render } from "@testing-library/react";
import { beforeAll, describe, expect, it } from "vitest";
import RankedBarChart from "./RankedBarChart.jsx";
import RiskTrendChart from "./RiskTrendChart.jsx";
import { charts } from "../theme.js";

const TREND = [
  { period: "2026-W01", value: 0.2 },
  { period: "2026-W02", value: 0.4 },
];

/** jsdom reports inline colours as `rgb(...)`, so compare against the token that way. */
function asRgb(hex) {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  return `rgb(${r}, ${g}, ${b})`;
}

beforeAll(() => {
  // Pretend every element has a 600x300 box, and provide the ResizeObserver
  // recharts expects to find in a browser.
  Object.defineProperty(HTMLElement.prototype, "getBoundingClientRect", {
    configurable: true,
    value: () => ({ width: 600, height: 300, top: 0, left: 0, right: 600, bottom: 300, x: 0, y: 0, toJSON: () => ({}) }),
  });
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

describe("RiskTrendChart", () => {
  it("actually renders the series (so the checks below are meaningful)", () => {
    const { container } = render(<RiskTrendChart data={TREND} />);
    expect(container.querySelector(".recharts-surface")).not.toBeNull();
    expect(container.innerHTML).toMatch(/recharts-area/);
  });

  it("draws no gradient: fills are flat, the line carries the trend", () => {
    const { container } = render(<RiskTrendChart data={TREND} />);
    expect(container.querySelector("linearGradient")).toBeNull();
    expect(container.querySelector("radialGradient")).toBeNull();
    // No shape may paint from a gradient reference (recharts' own clipPaths are
    // unrelated to colour and therefore not checked).
    const paintedFromGradient = [...container.querySelectorAll("[fill], [stroke]")].filter((el) =>
      /url\(#/.test(el.getAttribute("fill") || el.getAttribute("stroke") || ""),
    );
    expect(paintedFromGradient).toHaveLength(0);
  });

  it("paints the series from the shared palette", () => {
    const { container } = render(<RiskTrendChart data={TREND} />);
    const area = container.querySelector('[class*="recharts-area-area"]');
    expect(area).not.toBeNull();
    expect(area.getAttribute("fill")).toBe(charts.series.warning);
    expect(Number(area.getAttribute("fill-opacity"))).toBeLessThan(0.3); // a flat wash, not a gradient
  });

  it("still renders an empty state instead of an empty chart", () => {
    const { container } = render(<RiskTrendChart data={[]} />);
    expect(container.querySelector(".recharts-surface")).toBeNull();
  });
});

describe("RankedBarChart", () => {
  it("uses squared bars, never pill-shaped ends", () => {
    const { container } = render(<RankedBarChart items={[{ label: "RF power", value: 0.62 }]} />);
    expect(container.querySelectorAll(".rounded-full").length).toBe(0);
    expect(container.querySelectorAll(".rounded-sm").length).toBeGreaterThanOrEqual(2); // track + fill
  });

  it("fills bars with the accent token by default", () => {
    const { container } = render(<RankedBarChart items={[{ label: "RF power", value: 0.62 }]} />);
    const fill = container.querySelector(".rounded-sm > .rounded-sm");
    expect(fill.style.backgroundColor).toBe(asRgb(charts.series.accent));
  });
});
