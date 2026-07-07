import { BoltMark, InfoIcon, RefreshIcon } from "./icons";

export const TABS = ["Today's Picks", "Game Predictions", "Performance", "Soft Lines"] as const;
export type Tab = (typeof TABS)[number];

// Sticky quant header: gradient logo tile + wordmark, pill tab bar (active =
// brand gradient), and an "as of" line + gradient Refresh. No wallet/balance.
export function TopNav({
  tab,
  onTab,
  asOf,
  onRefresh,
  onHelp,
}: {
  tab: Tab;
  onTab: (t: Tab) => void;
  asOf: string;
  onRefresh: () => void;
  onHelp: () => void;
}) {
  return (
    <header className="sticky top-0 z-20 border-b border-hair bg-[rgba(9,9,15,0.82)] backdrop-blur-[14px]">
      <div className="mx-auto flex h-16 max-w-[1400px] items-center gap-5 px-5">
        {/* logo */}
        <div className="flex items-center gap-2.5">
          <span className="flex h-8 w-8 items-center justify-center rounded-[9px] bg-brand text-[#0A0A11] shadow-btn">
            <BoltMark className="h-4 w-4" />
          </span>
          <span className="text-[17px] font-extrabold tracking-tight text-ink">
            prop
            <span className="bg-brand bg-clip-text text-transparent">-edge</span>
          </span>
        </div>

        {/* tab pill bar */}
        <nav className="no-scrollbar ml-1 flex items-center gap-0.5 overflow-x-auto rounded-xl border border-white/5 bg-white/[0.03] p-1">
          {TABS.map((t) => (
            <button
              key={t}
              onClick={() => onTab(t)}
              className={[
                "tap-target inline-flex shrink-0 items-center justify-center rounded-lg px-3.5 py-1.5 text-[13px] font-semibold transition",
                t === tab ? "bg-brand text-[#0A0A11]" : "text-ink-3 hover:text-ink",
              ].join(" ")}
            >
              {t}
            </button>
          ))}
        </nav>

        <div className="flex-1" />

        {/* as-of + help + refresh */}
        <div className="flex items-center gap-2.5 sm:gap-3.5">
          <span className="hidden text-right text-[11px] leading-tight text-ink-4 sm:block">
            as of <b className="tnum text-ink-2">{asOf}</b>
            <br />
            new slate each morning
          </span>
          <button
            onClick={onHelp}
            aria-label="How this works"
            className="tap-target flex items-center justify-center gap-1.5 rounded-[11px] border border-hair bg-white/[0.03] px-3 py-2 text-[13px] font-semibold text-ink-2 transition hover:text-ink"
          >
            <InfoIcon className="h-4 w-4" />
            <span className="hidden sm:inline">How this works</span>
          </button>
          <button
            onClick={onRefresh}
            className="tap-target flex items-center justify-center gap-1.5 rounded-[11px] bg-brand px-3.5 py-2 text-[13px] font-semibold text-[#0A0A11] shadow-btn transition hover:brightness-105"
          >
            <RefreshIcon className="h-4 w-4" />
            <span className="hidden sm:inline">Refresh</span>
          </button>
        </div>
      </div>
    </header>
  );
}
