import { useMemo } from "react";
import type { Pick, SortKey } from "../types";
import { PickCard } from "./PickCard";

// Sorts (default Edge desc = best leans first) then lays out the responsive grid.
export function PickBoard({
  picks,
  sort,
  watched,
  onToggleWatch,
}: {
  picks: Pick[];
  sort: SortKey;
  watched: Set<string>;
  onToggleWatch: (id: string) => void;
}) {
  const sorted = useMemo(() => {
    const arr = [...picks];
    if (sort === "edge") {
      arr.sort((a, b) => Math.abs(b.edge_pct) - Math.abs(a.edge_pct));
    } else if (sort === "confidence") {
      arr.sort(
        (a, b) => b.model_confidence - a.model_confidence || Math.abs(b.edge_pct) - Math.abs(a.edge_pct),
      );
    } else {
      arr.sort((a, b) => (a.start_time ?? "").localeCompare(b.start_time ?? ""));
    }
    return arr;
  }, [picks, sort]);

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
      {sorted.map((p) => (
        <PickCard
          key={p.id}
          pick={p}
          watched={watched.has(p.id)}
          onToggleWatch={() => onToggleWatch(p.id)}
        />
      ))}
    </div>
  );
}
