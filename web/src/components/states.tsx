// Loading (skeleton), empty, and error states for the board.

export function SkeletonCard() {
  return (
    <div className="rounded-2xl border border-white/5 bg-surface p-4">
      <div className="flex items-center gap-3">
        <div className="h-14 w-14 rounded-full bg-white/10 animate-pulse-soft" />
        <div className="flex-1 space-y-2">
          <div className="h-3 w-2/3 rounded bg-white/10 animate-pulse-soft" />
          <div className="h-2.5 w-1/2 rounded bg-white/5 animate-pulse-soft" />
        </div>
      </div>
      <div className="mt-5 flex flex-col items-center gap-2">
        <div className="h-9 w-20 rounded bg-white/10 animate-pulse-soft" />
        <div className="h-2.5 w-24 rounded bg-white/5 animate-pulse-soft" />
      </div>
      <div className="mt-5 grid grid-cols-2 gap-2">
        <div className="h-9 rounded-full bg-white/10 animate-pulse-soft" />
        <div className="h-9 rounded-full bg-white/5 animate-pulse-soft" />
      </div>
    </div>
  );
}

export function SkeletonBoard() {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      {Array.from({ length: 8 }).map((_, i) => (
        <SkeletonCard key={i} />
      ))}
    </div>
  );
}

export function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-white/10 bg-surface/40 py-20 text-center">
      <div className="mb-3 text-4xl">📉</div>
      <p className="text-lg font-semibold text-ink">No edges found for this filter</p>
      <p className="mt-1 max-w-sm text-sm text-ink-dim">
        Try another league or stat type — the board only shows props the model has
        a live projection for today.
      </p>
    </div>
  );
}

export function ErrorState({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-coral/30 bg-coral/5 py-20 text-center">
      <div className="mb-3 text-4xl">⚠️</div>
      <p className="text-lg font-semibold text-ink">Couldn’t load picks</p>
      <p className="mt-1 max-w-sm text-sm text-ink-dim">
        The prediction API didn’t respond. Check that it’s running, then retry.
      </p>
      <button
        onClick={onRetry}
        className="mt-5 rounded-full bg-violet px-5 py-2 text-sm font-semibold text-white shadow-violet-soft transition hover:brightness-110"
      >
        Retry
      </button>
    </div>
  );
}
