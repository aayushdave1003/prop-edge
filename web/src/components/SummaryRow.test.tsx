import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { SummaryRow } from "./SummaryRow";
import type { Summary } from "../types";

const summary: Summary = {
  today: 171,
  recommended: 8,
  avg_edge_pct: 13.5,
  w: 402,
  l: 415,
  win_rate_pct: 49,
};

describe("SummaryRow", () => {
  it("renders the KPI labels and the fixed breakeven card", () => {
    render(<SummaryRow summary={summary} />);
    expect(screen.getByText("Today's Board")).toBeInTheDocument();
    expect(screen.getByText("Breakeven")).toBeInTheDocument();
    // breakeven is a constant, always honest
    expect(screen.getByText("57.7%")).toBeInTheDocument();
    // 7-day record renders W–L
    expect(screen.getByText("402–415")).toBeInTheDocument();
  });

  it("renders em-dash placeholders when there is no summary", () => {
    render(<SummaryRow summary={null} />);
    // 4 of the 5 cards fall back to "—" (Breakeven stays 57.7%)
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(4);
    expect(screen.getByText("57.7%")).toBeInTheDocument();
  });
});
