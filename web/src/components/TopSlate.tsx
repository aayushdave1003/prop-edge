import { useState } from "react";
import type { TopSlate as TopSlateT } from "../types";
import { Avatar } from "./Avatar";
import { BoltMark } from "./icons";

// Correlation-aware Top-N slate. Money sizing is paper / hypothetical. "Tail
// slate · copy" writes a text summary to the clipboard (no bet action).
export function TopSlate({ slate }: { slate: TopSlateT }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    const text =
      `prop-edge — Top ${slate.n}-leg slate (paper-tracking, hypothetical)\n` +
      slate.legs
        .map((l) => `${l.player} (${l.league.toUpperCase()}) ${l.stat_type} ${l.recommendation.toUpperCase()} ${l.line} · ${l.confidence}%`)
        .join("\n");
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      /* clipboard blocked */
    }
  }

  return (
    <div
      className="rounded-[18px] border border-accent-border p-4"
      style={{ background: "linear-gradient(180deg, rgba(124,92,255,0.08) 0%, rgba(124,92,255,0.015) 46%, transparent 100%)" }}
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="flex h-9 w-9 items-center justify-center rounded-[11px] bg-brand text-[#0A0A11] shadow-btn">
            <BoltMark className="h-4 w-4" />
          </span>
          <div>
            <div className="text-[16px] font-extrabold tracking-tight text-ink">
              Top {slate.n}-Leg Slate{" "}
              <span className="tnum font-bold text-accent">{slate.payout}× payout</span>
            </div>
            <div className="mt-0.5 text-[11.5px] text-ink-3">
              Correlation-aware · {slate.games} games · {slate.joint_hit_pct}% joint hit (if
              independent) · paper sizing
            </div>
          </div>
        </div>
        <button
          onClick={copy}
          className="rounded-[11px] border border-accent-border bg-accent-soft px-3.5 py-2 text-[12.5px] font-semibold text-accent transition hover:brightness-110"
        >
          {copied ? "Copied ✓" : "Tail slate · copy"}
        </button>
      </div>

      <div className="mt-3.5 grid grid-cols-1 gap-2.5 sm:grid-cols-2 xl:grid-cols-4">
        {slate.legs.map((l, i) => {
          const over = l.recommendation === "over";
          return (
            <div key={i} className="flex items-center gap-2.5 rounded-[13px] border border-hair bg-black/20 p-2.5">
              <Avatar name={l.player} src={null} size={34} />
              <div className="min-w-0 flex-1">
                <div className="truncate text-[13px] font-bold text-ink">
                  {l.player}
                  <span className="ml-1.5 text-[9.5px] font-bold uppercase text-ink-4">{l.league}</span>
                </div>
                <div className="tnum mt-0.5 truncate text-[11px] text-ink-3">
                  {l.stat_type} · {l.line} · {l.confidence}%
                  {l.stake_pct != null && <span className="text-accent"> · {l.stake_pct}%</span>}
                </div>
              </div>
              <span
                className={[
                  "shrink-0 rounded-lg px-2 py-1 text-[11px] font-extrabold uppercase",
                  over ? "bg-pos/[0.14] text-pos" : "bg-neg/[0.14] text-neg",
                ].join(" ")}
              >
                {over ? "▲" : "▼"}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
