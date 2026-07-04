import { useMemo } from "react";
import type { Pick, SortKey } from "../types";
import { PickCard } from "./PickCard";

// Recommended (edge) picks always first, then by the active sort key.
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
    const key = (a: Pick, b: Pick) => {
      if (sort === "confidence") return b.model_confidence - a.model_confidence;
      if (sort === "start") return (a.start_time ?? "").localeCompare(b.start_time ?? "");
      return Math.abs(b.edge_pct) - Math.abs(a.edge_pct);
    };
    return [...picks].sort((a, b) => Number(b.recommended) - Number(a.recommended) || key(a, b));
  }, [picks, sort]);

  return (
    <div
      className="grid gap-4"
      style={{ gridTemplateColumns: "repeat(auto-fill, minmax(322px, 1fr))" }}
    >
      {sorted.map((p, i) => (
        <PickCard
          key={p.id}
          pick={p}
          rank={i + 1}
          watched={watched.has(p.id)}
          onToggleWatch={() => onToggleWatch(p.id)}
        />
      ))}
    </div>
  );
}
