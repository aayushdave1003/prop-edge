import type { Performance as Perf, Verdict } from "../types";

const BE = 57.7; // per-leg 2-pick parlay breakeven (used for chart geometry)

const VERDICT_TEXT: Record<Verdict, string> = {
  edge: "clears breakeven",
  "not proven": "not proven",
  "below breakeven": "below breakeven",
  "—": "—",
};
// green ONLY when the CI floor actually clears breakeven; otherwise muted/amber.
// The big numbers are never green unless genuinely proven.
function verdictColor(v?: Verdict): string {
  return v === "edge" ? "#34D399" : v === "not proven" ? "#F5B544" : "#9A9AA8";
}

export function PerformanceView({ perf, loading }: { perf: Perf | null; loading: boolean }) {
  if (loading || !perf) {
    return (
      <div className="space-y-4">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-24 rounded-[16px] border border-hair bg-panel animate-pulse-soft" />
          ))}
        </div>
        <div className="h-56 rounded-[16px] border border-hair bg-panel animate-pulse-soft" />
      </div>
    );
  }

  const rec = perf.recommended;
  const be = perf.breakeven ?? BE;
  const recEdge = rec.verdict === "edge";
  const relation = recEdge ? "above" : rec.verdict === "not proven" ? "around" : "below";

  return (
    <div className="space-y-4">
      <div className="text-[13px] text-ink-2">
        <b className="text-ink">Track record</b> · settled picks · paper / hypothetical
        <span className="ml-1 text-ink-4">· {perf.method}</span>
      </div>

      {/* honesty callout — states plainly what the number is and isn't */}
      <div
        className="rounded-[14px] border px-4 py-3 text-[12.5px] leading-relaxed text-ink-2"
        style={{ borderColor: "rgba(245,181,68,0.28)", background: "rgba(245,181,68,0.06)" }}
      >
        <b className="text-ink">How to read this.</b> The recommended-tier rate is measured{" "}
        <b>point-in-time</b> (every cutoff sees only picks settled before it), <b>forward-only</b>{" "}
        (picks logged after game start are excluded), and <b>valid-line-only</b> (picks with no prop
        line — nothing to be right or wrong about — are dropped). At{" "}
        <b className="tnum text-ink">{rec.pct}%</b> [{rec.lo}–{rec.hi}%] it sits {relation} the{" "}
        <b>{be}%</b> parlay breakeven — <b className="text-ink">not a proven edge</b>. An earlier
        "~72%" headline was an in-sample measurement artifact (the cutoff had seen the outcomes it was
        scored on), not a forward result.
      </div>

      {/* headline cards */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Headline
          label="Recommended Tier · out-of-sample"
          value={`${rec.pct}%`}
          color={recEdge ? "#34D399" : "#ECECF2"}
          sub={`${rec.w}W–${rec.l}L · 95% CI ${rec.lo}–${rec.hi}%`}
          chip={{ text: VERDICT_TEXT[rec.verdict ?? "—"], color: verdictColor(rec.verdict) }}
        />
        <Headline
          label="All Logged Picks"
          value={`${perf.all_picks.pct}%`}
          color="#ECECF2"
          sub={`${perf.all_picks.w}W–${perf.all_picks.l}L · forward-only · valid-line`}
        />
        <Headline
          label="Closing Line Value"
          value={`${perf.clv_pct >= 0 ? "+" : ""}${perf.clv_pct}%`}
          color="#22D3EE"
          sub="avg no-vig move vs our side"
        />
      </div>

      {/* rolling trend */}
      <Panel title="Rolling rec-tier win-rate (out-of-sample)">
        <TrendChart points={perf.trend.map((t) => t.pct)} be={be} />
      </Panel>

      {/* by sport + roi */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Panel title="Recommended-tier by sport · with 95% CI">
          <div className="space-y-3">
            {perf.by_sport.map((s) => (
              <SportRow key={s.sport} s={s} be={be} />
            ))}
          </div>
        </Panel>
        <Panel title="Paper ROI by sport (flat 1u @ breakeven odds)">
          <div className="space-y-2.5">
            {perf.roi_by_sport.map((s) => (
              <RoiRow
                key={s.sport}
                label={s.sport}
                roi={s.roi}
                max={Math.max(1, ...perf.roi_by_sport.map((x) => Math.abs(x.roi)))}
              />
            ))}
          </div>
        </Panel>
      </div>

      {/* calibration + per-bucket diagnostic */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Panel title="Calibration" right={perf.brier != null ? `Brier ${perf.brier}` : undefined}>
          <Calibration bins={perf.calibration} />
        </Panel>
        <Panel title="Per-bucket rate · diagnostic (small samples)">
          <div className="mb-2.5 text-[11px] leading-snug text-ink-4">
            Exploratory only — not headline claims. Single-bucket rates on small samples are
            unreliable and can be distorted by backfilled picks (the killed mlb·hits·under "edge"
            is documented in <span className="text-ink-3">mirage_analysis_mlb_hits_under</span>). A
            high number here is not an edge.
          </div>
          <div className="space-y-1.5">
            {perf.by_market.map((m, i) => (
              <div key={i} className="flex items-center justify-between text-[12.5px]">
                <span className="text-ink-2">
                  {m.market}{" "}
                  <span className={m.lean === "over" ? "text-pos" : "text-neg"}>{m.lean.toUpperCase()}</span>
                  <span className="tnum ml-1.5 text-ink-4">
                    n={m.n} · [{m.lo}–{m.hi}%]
                  </span>
                </span>
                {/* never coloured as an edge — a single bucket is not a claim */}
                <span className="tnum font-semibold text-ink-2">{m.pct}%</span>
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </div>
  );
}

function Headline({
  label,
  value,
  color,
  sub,
  chip,
}: {
  label: string;
  value: string;
  color: string;
  sub: string;
  chip?: { text: string; color: string };
}) {
  return (
    <div className="rounded-[16px] border border-hair bg-panel p-4">
      <div className="microlabel">{label}</div>
      <div className="mt-1.5 flex items-end gap-2">
        <div className="tnum text-[30px] font-bold leading-none tracking-tight" style={{ color }}>
          {value}
        </div>
        {chip && (
          <span
            className="mb-0.5 rounded-full px-2 py-0.5 text-[10px] font-semibold"
            style={{ color: chip.color, background: `${chip.color}1f` }}
          >
            {chip.text}
          </span>
        )}
      </div>
      <div className="tnum mt-2 text-[11px] text-ink-3">{sub}</div>
    </div>
  );
}

function Panel({ title, right, children }: { title: string; right?: string; children: React.ReactNode }) {
  return (
    <div className="rounded-[16px] border border-hair bg-panel p-[18px]">
      <div className="mb-3.5 flex items-center justify-between">
        <div className="microlabel">{title}</div>
        {right && <div className="tnum text-[11px] text-ink-3">{right}</div>}
      </div>
      {children}
    </div>
  );
}

function TrendChart({ points, be }: { points: number[]; be: number }) {
  const W = 600;
  const H = 150;
  if (points.length < 2) return <div className="py-8 text-center text-[12px] text-ink-3">Not enough settled picks yet.</div>;
  const lo = 40;
  const hi = 100;
  const x = (i: number) => (i / (points.length - 1)) * W;
  const y = (v: number) => H - ((v - lo) / (hi - lo)) * H;
  const line = points.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p).toFixed(1)}`).join(" ");
  const area = `${line} L${W},${H} L0,${H} Z`;
  const beY = y(be);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-[150px] w-full" preserveAspectRatio="none">
      <defs>
        <linearGradient id="trendFill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#7C5CFF" stopOpacity="0.28" />
          <stop offset="100%" stopColor="#7C5CFF" stopOpacity="0" />
        </linearGradient>
      </defs>
      <line x1="0" y1={beY} x2={W} y2={beY} stroke="#F5B544" strokeWidth="1" strokeDasharray="5 4" opacity="0.7" />
      <path d={area} fill="url(#trendFill)" />
      <path d={line} fill="none" stroke="#7C5CFF" strokeWidth="2" />
    </svg>
  );
}

// Per-sport rec-tier: muted bar (grey unless the CI floor clears breakeven),
// explicit 95% CI text, breakeven marker, and an honest verdict label.
function SportRow({ s, be }: { s: Perf["by_sport"][number]; be: number }) {
  const edge = s.verdict === "edge";
  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-[12px]">
        <span className="text-ink-2">{s.sport}</span>
        <span className="tnum text-ink-3">
          <b className="tnum" style={{ color: edge ? "#34D399" : "#ECECF2" }}>
            {s.pct}%
          </b>
          <span className="ml-1.5 text-ink-4">
            [{s.lo}–{s.hi}%]
          </span>{" "}
          · {s.w}–{s.l}
        </span>
      </div>
      <div className="relative h-2 rounded bg-white/[0.05]">
        <div className="h-full rounded" style={{ width: `${s.pct}%`, background: edge ? "#34D399" : "#9A9AA8" }} />
        <div className="absolute -top-0.5 bottom-[-2px] w-[1.5px] bg-warn opacity-80" style={{ left: `${be}%` }} />
      </div>
      <div className="mt-1 text-[10.5px] font-medium" style={{ color: verdictColor(s.verdict) }}>
        {VERDICT_TEXT[s.verdict]}
      </div>
    </div>
  );
}

function RoiRow({ label, roi, max }: { label: string; roi: number; max: number }) {
  return (
    <div>
      <div className="mb-1 flex justify-between text-[12px]">
        <span className="text-ink-2">{label}</span>
        <span className="tnum font-semibold" style={{ color: roi >= 0 ? "#7C5CFF" : "#F87171" }}>
          {roi >= 0 ? "+" : ""}
          {roi}%
        </span>
      </div>
      <div className="h-2 rounded bg-white/[0.05]">
        <div className="h-full rounded bg-accent opacity-85" style={{ width: `${(Math.abs(roi) / max) * 100}%` }} />
      </div>
    </div>
  );
}

function Calibration({ bins }: { bins: { pred: number; actual: number }[] }) {
  const lo = 40;
  const hi = 95;
  const map = (v: number) => ((v - lo) / (hi - lo)) * 100;
  return (
    <svg viewBox="0 0 100 100" className="mx-auto block aspect-square w-full max-w-[220px]">
      <rect x="0" y="0" width="100" height="100" fill="none" />
      <line x1="0" y1="100" x2="100" y2="0" stroke="#565663" strokeWidth="0.8" strokeDasharray="3 3" />
      {bins.map((b, i) => (
        <circle key={i} cx={map(b.pred)} cy={100 - map(b.actual)} r="3.2" fill="#7C5CFF" stroke="#12101d" strokeWidth="0.8" />
      ))}
    </svg>
  );
}
