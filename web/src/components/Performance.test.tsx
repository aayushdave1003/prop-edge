import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { PerformanceView } from "./Performance";
import type { Performance } from "../types";

// A perf snapshot whose recommended tier is honestly BELOW breakeven. The whole
// point of these tests is that the UI keeps that framing: shows the CI + verdict
// and never paints the headline green.
const perf: Performance = {
  recommended: { pct: 47.1, w: 120, l: 135, n: 255, lo: 44.0, hi: 50.2, verdict: "below breakeven" },
  all_picks: { pct: 48.0, w: 400, l: 433, lo: 44.6, hi: 51.4 },
  clv_pct: -0.3,
  breakeven: 57.7,
  method: "forward-only, point-in-time, valid-line-only",
  trend: [
    { i: 0, pct: 46 },
    { i: 1, pct: 47 },
    { i: 2, pct: 47.1 },
  ],
  by_sport: [{ sport: "MLB", w: 60, l: 70, pct: 46.2, lo: 40, hi: 52, verdict: "below breakeven" }],
  roi_by_sport: [{ sport: "MLB", roi: -4.2 }],
  calibration: [{ pred: 55, actual: 52, n: 100 }],
  brier: 0.24,
  by_market: [{ market: "MLB · Hits", lean: "under", pct: 62, n: 9, lo: 30, hi: 86 }],
};

const GREEN = "#34D399";

describe("PerformanceView", () => {
  it("renders a skeleton while loading", () => {
    const { container } = render(<PerformanceView perf={null} loading />);
    expect(container.querySelector(".animate-pulse-soft")).toBeTruthy();
  });

  it("states the honest verdict and the CI, sitting below breakeven", () => {
    const { container } = render(<PerformanceView perf={perf} loading={false} />);
    // the honest rate surfaces (callout + headline)
    expect(screen.getAllByText("47.1%").length).toBeGreaterThan(0);
    expect(screen.getByText(/not a proven edge/i)).toBeInTheDocument();
    // "below breakeven" verdict text is shown (headline chip + sport row)
    expect(screen.getAllByText(/below breakeven/i).length).toBeGreaterThan(0);
    // the CI bounds render somewhere on the page (text is split across nodes)
    expect(container.textContent).toContain("44");
    expect(container.textContent).toContain("50.2");
  });

  it("does NOT color the recommended headline green when below breakeven", () => {
    render(<PerformanceView perf={perf} loading={false} />);
    // The headline value node carries an inline color. Find the big "47.1%" that
    // is the headline (30px) and assert it is ink, not the green edge color.
    const headline = screen
      .getAllByText("47.1%")
      .find((el) => el.className.includes("text-[30px]"));
    expect(headline).toBeTruthy();
    expect(headline!).not.toHaveStyle({ color: GREEN });
    expect(headline!).toHaveStyle({ color: "#ECECF2" });
  });

  it("colors the headline green only when the verdict is a proven edge", () => {
    const edgePerf: Performance = {
      ...perf,
      recommended: { ...perf.recommended, pct: 61.0, lo: 58.2, hi: 63.5, verdict: "edge" },
    };
    render(<PerformanceView perf={edgePerf} loading={false} />);
    const headline = screen
      .getAllByText("61%")
      .find((el) => el.className.includes("text-[30px]"));
    expect(headline).toBeTruthy();
    expect(headline!).toHaveStyle({ color: GREEN });
  });

  it("keeps small-sample per-bucket rates uncolored (never an edge claim)", () => {
    const { container } = render(<PerformanceView perf={perf} loading={false} />);
    // the single-bucket value span ("62" + "%" render as separate text nodes)
    // must not be green — a single bucket is never presented as an edge.
    const bucket = [...container.querySelectorAll("span.tnum.font-semibold.text-ink-2")].find(
      (el) => el.textContent === "62%",
    ) as HTMLElement | undefined;
    expect(bucket).toBeTruthy();
    expect(bucket!).not.toHaveStyle({ color: GREEN });
    // and it lives in the diagnostic panel carrying the small-samples warning
    expect(screen.getByText(/diagnostic \(small samples\)/i)).toBeInTheDocument();
  });
});
