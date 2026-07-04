// Loading (skeleton), empty, and error states for the board.

export function SkeletonCard() {
  return (
    <div className="rounded-[18px] border border-hair bg-card-std p-4">
      <div className="flex items-center gap-3">
        <div className="h-[46px] w-[46px] rounded-full bg-white/10 animate-pulse-soft" />
        <div className="flex-1 space-y-2">
          <div className="h-3 w-2/3 rounded bg-white/10 animate-pulse-soft" />
          <div className="h-2.5 w-1/2 rounded bg-white/5 animate-pulse-soft" />
        </div>
      </div>
      <div className="mt-4 h-20 rounded-[14px] bg-white/[0.04] animate-pulse-soft" />
      <div className="mt-4 h-2 rounded bg-white/10 animate-pulse-soft" />
      <div className="mt-3 h-2 rounded bg-white/5 animate-pulse-soft" />
    </div>
  );
}

export function SkeletonBoard() {
  return (
    <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(322px, 1fr))" }}>
      {Array.from({ length: 8 }).map((_, i) => (
        <SkeletonCard key={i} />
      ))}
    </div>
  );
}

export function EmptyState() {
  return (
    <div className="rounded-[18px] border border-dashed border-white/10 bg-white/[0.012] px-5 py-[70px] text-center">
      <div className="text-[15px] font-semibold text-ink-2">No leans match these filters</div>
      <div className="mt-1.5 text-[12.5px] text-ink-3">Loosen the market, direction, or edge-only filter.</div>
    </div>
  );
}

export function ErrorState({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="rounded-[18px] border border-neg/30 bg-neg/[0.05] px-5 py-[70px] text-center">
      <div className="text-[15px] font-semibold text-ink">Couldn’t load the board</div>
      <div className="mt-1.5 text-[12.5px] text-ink-3">The prediction API didn’t respond.</div>
      <button
        onClick={onRetry}
        className="mt-5 rounded-[11px] bg-brand px-5 py-2 text-[13px] font-semibold text-[#0A0A11] shadow-btn transition hover:brightness-105"
      >
        Retry
      </button>
    </div>
  );
}
