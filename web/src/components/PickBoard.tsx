import { useMemo } from "react";
import type { Pick, SortKey } from "../types";
import { PickCard } from "./PickCard";

const CONF_RANK: Record<string, number> = { high: 3, med: 2, low: 1 };

// Sorts (default Edge desc = best plays first) then lays out the responsive grid.
export function PickBoard({ picks, sort }: { picks: Pick[]; sort: SortKey }) {
  const sorted = useMemo(() => {
    const arr = [...picks];
    if (sort === "edge") {
      // strongest absolute edge first — both big overs and big unders are plays
      arr.sort((a, b) => Math.abs(b.edge_pct) - Math.abs(a.edge_pct));
    } else if (sort === "confidence") {
      arr.sort(
        (a, b) =>
          (CONF_RANK[b.confidence] ?? 0) - (CONF_RANK[a.confidence] ?? 0) ||
          Math.abs(b.edge_pct) - Math.abs(a.edge_pct),
      );
    } else {
      arr.sort((a, b) => (a.start_time ?? "").localeCompare(b.start_time ?? ""));
    }
    return arr;
  }, [picks, sort]);

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      {sorted.map((p) => (
        <PickCard key={p.id} pick={p} />
      ))}
    </div>
  );
}
