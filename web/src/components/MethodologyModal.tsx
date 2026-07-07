import { useEffect, useRef } from "react";

// "How this works" — a lightweight, accessible methodology dialog. Its whole job
// is to keep the framing honest: research / paper-tracking only, what the labels
// mean, and that the tracked rate sits BELOW breakeven (not a proven edge).
export function MethodologyModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const panelRef = useRef<HTMLDivElement>(null);

  // Close on Escape; move focus into the dialog when it opens.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    panelRef.current?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 p-0 backdrop-blur-[2px] sm:items-center sm:p-5"
      onClick={onClose}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="methodology-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        className="max-h-[88vh] w-full max-w-[560px] overflow-y-auto rounded-t-[20px] border border-hair bg-[#0E0E16] p-5 shadow-btn outline-none sm:rounded-[20px] sm:p-6"
      >
        {/* header */}
        <div className="flex items-start justify-between gap-4">
          <div>
            <div id="methodology-title" className="text-[17px] font-extrabold tracking-tight text-ink">
              How this works
            </div>
            <div className="mt-1 text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
              Research · Paper-tracking · Model leans, not betting advice
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="tap-target -mr-1.5 -mt-1.5 flex items-center justify-center rounded-lg px-2 py-1.5 text-ink-3 transition hover:text-ink"
          >
            <CloseIcon className="h-4 w-4" />
          </button>
        </div>

        {/* honest verdict callout — mirrors the Performance tab framing */}
        <div
          className="mt-4 rounded-[14px] border px-4 py-3 text-[12.5px] leading-relaxed text-ink-2"
          style={{ borderColor: "rgba(245,181,68,0.28)", background: "rgba(245,181,68,0.06)" }}
        >
          The tracked recommended-tier rate is <b className="tnum text-ink">47.1%</b>, which is{" "}
          <b className="text-ink">below</b> the <b>57.7%</b> two-leg parlay breakeven. That means this
          is <b className="text-ink">not a proven edge</b>. Treat everything here as hypothetical model
          output for research — never as advice to place a wager.
        </div>

        <div className="mt-4 space-y-4">
          <Item title="What this is">
            A self-running model that publishes a fresh slate each morning and tracks how its calls
            would have settled — on paper. No money is staked and nothing here is a recommendation to
            bet. It surfaces where the model disagrees with the posted line so you can study it.
          </Item>

          <Item title="“Model lean”">
            A lean is the side (<span className="text-pos">Over</span> or <span className="text-neg">Under</span>)
            the model projects relative to the posted line — not a pick to wager. The projection and a
            likely range show where the model lands versus that line.
          </Item>

          <Item title="“Recommended tier”">
            A subset of leans that clear the model’s internal per-market cutoff. “Recommended” means it
            cleared that bar — it does <b className="text-ink-2">not</b> mean it clears breakeven. Across
            settled picks the tier tracks at 47.1%, still under the 57.7% needed for a two-leg parlay to
            be profitable long-run.
          </Item>

          <Item title="Confidence & CIs">
            Model confidence is a calibrated probability (0–100%). Rates are shown with a 95% confidence
            interval, e.g. <span className="tnum text-ink-2">[44–50%]</span> — the plausible range given
            the sample. We only call something an “edge” when the <b>lower</b> bound of that interval
            clears 57.7%. When the interval straddles or sits below breakeven, it is “not proven” or
            “below breakeven” — and shown in muted colors, never green.
          </Item>

          <Item title="How rates are measured">
            Every reported rate is <b className="text-ink-2">forward-only</b> (picks logged after game
            start are excluded), <b className="text-ink-2">point-in-time</b> (each cutoff sees only picks
            settled before it), and <b className="text-ink-2">valid-line-only</b> (picks with no posted
            line are dropped). Single-market “edges” on small samples are treated as diagnostics, not
            claims.
          </Item>
        </div>

        <button
          onClick={onClose}
          className="tap-target mt-5 flex w-full items-center justify-center rounded-[11px] border border-hair bg-white/[0.03] py-2.5 text-[13px] font-semibold text-ink transition hover:bg-white/[0.06]"
        >
          Got it
        </button>
      </div>
    </div>
  );
}

function Item({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="microlabel">{title}</div>
      <p className="mt-1.5 text-[12.5px] leading-relaxed text-ink-2">{children}</p>
    </div>
  );
}

function CloseIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <path d="M6 6l12 12M18 6 6 18" strokeLinecap="round" />
    </svg>
  );
}
