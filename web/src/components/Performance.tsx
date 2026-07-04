import type { Performance as Perf } from "../types";

const BE = 57.7; // breakeven

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
  return (
    <div className="space-y-4">
      <div className="text-[13px] text-ink-2">
        <b className="text-ink">Track record</b> · settled picks · paper / hypothetical
      </div>

      {/* headline cards */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Headline
          label="Recommended Tier"
          value={`${rec.pct}%`}
          color="#34D399"
          sub={`${rec.w}W–${rec.l}L · ${rec.over_breakeven != null && rec.over_breakeven >= 0 ? "+" : ""}${rec.over_breakeven} pts over breakeven`}
        />
        <Headline label="All Logged Picks" value={`${perf.all_picks.pct}%`} color="#ECECF2" sub={`${perf.all_picks.w}W–${perf.all_picks.l}L`} />
        <Headline
          label="Closing Line Value"
          value={`${perf.clv_pct >= 0 ? "+" : ""}${perf.clv_pct}%`}
          color="#22D3EE"
          sub="avg move vs our side"
        />
      </div>

      {/* rolling trend */}
      <Panel title="Rolling rec-tier win-rate">
        <TrendChart points={perf.trend.map((t) => t.pct)} />
      </Panel>

      {/* by sport + roi */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Panel title="Win rate by sport">
          <div className="space-y-2.5">
            {perf.by_sport.map((s) => (
              <BarRow key={s.sport} label={s.sport} pct={s.pct} right={`${s.w}–${s.l}`} good={s.pct >= BE} />
            ))}
          </div>
        </Panel>
        <Panel title="Paper ROI by sport">
          <div className="space-y-2.5">
            {perf.roi_by_sport.map((s) => (
              <RoiRow key={s.sport} label={s.sport} roi={s.roi} max={Math.max(1, ...perf.roi_by_sport.map((x) => Math.abs(x.roi)))} />
            ))}
          </div>
        </Panel>
      </div>

      {/* calibration + market */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Panel title="Calibration" right={perf.brier != null ? `Brier ${perf.brier}` : undefined}>
          <Calibration bins={perf.calibration} />
        </Panel>
        <Panel title="Win rate by market × lean">
          <div className="space-y-1.5">
            {perf.by_market.map((m, i) => (
              <div key={i} className="flex items-center justify-between text-[12.5px]">
                <span className="text-ink-2">
                  {m.market}{" "}
                  <span className={m.lean === "over" ? "text-pos" : "text-neg"}>{m.lean.toUpperCase()}</span>
                  <span className="tnum ml-1.5 text-ink-4">n={m.n}</span>
                </span>
                <span className="tnum font-semibold" style={{ color: m.pct >= BE ? "#34D399" : "#ECECF2" }}>
                  {m.pct}%
                </span>
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </div>
  );
}

function Headline({ label, value, color, sub }: { label: string; value: string; color: string; sub: string }) {
  return (
    <div className="rounded-[16px] border border-hair bg-panel p-4">
      <div className="microlabel">{label}</div>
      <div className="tnum mt-1.5 text-[30px] font-bold leading-none tracking-tight" style={{ color }}>
        {value}
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

function TrendChart({ points }: { points: number[] }) {
  const W = 600;
  const H = 150;
  if (points.length < 2) return <div className="py-8 text-center text-[12px] text-ink-3">Not enough settled picks yet.</div>;
  const lo = 40;
  const hi = 100;
  const x = (i: number) => (i / (points.length - 1)) * W;
  const y = (v: number) => H - ((v - lo) / (hi - lo)) * H;
  const line = points.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p).toFixed(1)}`).join(" ");
  const area = `${line} L${W},${H} L0,${H} Z`;
  const beY = y(BE);
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

function BarRow({ label, pct, right, good }: { label: string; pct: number; right: string; good: boolean }) {
  return (
    <div>
      <div className="mb-1 flex justify-between text-[12px]">
        <span className="text-ink-2">{label}</span>
        <span className="tnum text-ink-3">
          <b className="tnum" style={{ color: good ? "#34D399" : "#ECECF2" }}>{pct}%</b> · {right}
        </span>
      </div>
      <div className="relative h-2 rounded bg-white/[0.05]">
        <div className="h-full rounded" style={{ width: `${pct}%`, background: good ? "#34D399" : "#9A9AA8" }} />
        <div className="absolute -top-0.5 bottom-[-2px] w-[1.5px] bg-warn opacity-80" style={{ left: `${BE}%` }} />
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
