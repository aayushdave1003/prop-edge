import { useState } from "react";
import type { TopSlate as TopSlateT } from "../types";

// The diversified "Top N-Pick Slate" hero. Money sizing is labeled paper /
// hypothetical — never betting advice. "Tail" copies text only (no bet action).
export function TopSlate({ slate }: { slate: TopSlateT }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  const copyText = slate.legs
    .map(
      (l) =>
        `${l.player} (${l.league.toUpperCase()}) ${l.stat_type} ${l.recommendation.toUpperCase()} ${l.line} — ${l.confidence}%`,
    )
    .join("\n");

  async function copy() {
    try {
      await navigator.clipboard.writeText(
        `prop-edge — Top ${slate.n}-pick slate (paper-tracking, hypothetical)\n${copyText}`,
      );
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      /* clipboard blocked — no-op */
    }
  }

  return (
    <div className="rounded-2xl border border-violet/40 bg-surface/80 p-4 shadow-violet-soft">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-base font-extrabold text-ink">
          ⚡ Top {slate.n}-Pick Slate ·{" "}
          <span className="text-violet">{slate.payout}× payout</span>
        </h2>
        <span className="text-xs text-ink-dim">
          Diversified across {slate.games} games · {slate.joint_hit_pct}% joint hit (if
          independent) · paper-tracking only
        </span>
      </div>

      <ul className="mt-3 divide-y divide-white/5">
        {slate.legs.map((l, i) => {
          const over = l.recommendation === "over";
          return (
            <li key={i} className="flex items-center justify-between gap-3 py-2">
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold text-ink">
                  {l.player}
                  <span className="ml-2 text-[11px] font-bold uppercase text-ink-dim">
                    {l.league}
                  </span>
                </div>
                <div className="text-xs text-ink-dim">
                  {l.stat_type} · {l.line} · {l.confidence}% conf
                  {l.stake_pct != null && (
                    <span className="text-violet"> · stake {l.stake_pct}% (paper)</span>
                  )}
                </div>
              </div>
              <span
                className={[
                  "shrink-0 rounded-full px-3 py-1 text-xs font-bold uppercase",
                  over ? "bg-mint/15 text-mint" : "bg-coral/15 text-coral",
                ].join(" ")}
              >
                {over ? "▲ Over" : "▼ Under"}
              </span>
            </li>
          );
        })}
      </ul>

      {/* slate-Kelly explainer — clearly paper sizing */}
      <p className="mt-3 rounded-lg bg-violet/5 px-3 py-2 text-[11px] leading-snug text-ink-dim">
        <span className="font-semibold text-violet">Slate-Kelly stakes · paper sizing, not betting advice.</span>{" "}
        Correlation-aware half-Kelly over the joint outcome distribution
        {slate.max_stake_pct != null && <> · capped at {slate.max_stake_pct}% of bankroll per leg</>}.
        Hypothetical only.
      </p>

      {/* tail (copy text only) */}
      <div className="mt-3">
        <button
          onClick={() => setOpen((o) => !o)}
          className="text-xs font-semibold text-util-blue underline underline-offset-2"
        >
          {open ? "Hide" : `Tail this slate — copy ${slate.n} picks`}
        </button>
        {open && (
          <div className="mt-2 rounded-lg bg-bg-deep p-3">
            <pre className="no-scrollbar overflow-x-auto whitespace-pre-wrap text-[11px] text-ink-dim">
              {copyText}
            </pre>
            <button
              onClick={copy}
              className="mt-2 rounded-full bg-violet px-3 py-1.5 text-xs font-semibold text-white transition hover:brightness-110"
            >
              {copied ? "Copied ✓" : "Copy picks"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
