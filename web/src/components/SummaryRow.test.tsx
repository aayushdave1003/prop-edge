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
  it("renders the KPI labels and the audited tracked-tier card", () => {
    render(<SummaryRow summary={summary} />);
    expect(screen.getByText("Today's Board")).toBeInTheDocument();
    expect(screen.getByText("Tracked Tier")).toBeInTheDocument();
    // The audited rec-tier number and the breakeven bar are shown TOGETHER, so
    // the tier can only ever be read against the bar it is actually below.
    expect(screen.getByText("47.3%")).toBeInTheDocument();
    expect(screen.getByText(/57\.7% breakeven/)).toBeInTheDocument();
    // raw volume renders W–L
    expect(screen.getByText("402–415")).toBeInTheDocument();
  });

  it("labels the raw rate as volume, never as the tracked record", () => {
    render(<SummaryRow summary={summary} />);
    // 49% here is every logged leg, dominated by lopsided base-rate markets
    // (MLB RBI-overs hit ~70% unaided). Calling it a hit/win rate next to a
    // breakeven bar invited the conclusion that the system is profitable.
    expect(screen.getByText("7-Day Volume")).toBeInTheDocument();
    expect(screen.getByText("49% of all logged legs")).toBeInTheDocument();
    expect(screen.queryByText(/49% hit/)).not.toBeInTheDocument();
  });

  it("renders em-dash placeholders when there is no summary", () => {
    render(<SummaryRow summary={null} />);
    // 4 of the 5 cards fall back to "—" (Tracked Tier is a constant)
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(4);
    expect(screen.getByText("47.3%")).toBeInTheDocument();
  });
});
