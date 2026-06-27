import { BoltMark, RefreshIcon } from "./icons";

export const TABS = [
  "Today's Picks",
  "Game Predictions",
  "Performance",
  "Soft Lines",
  "Recent Picks",
] as const;
export type Tab = (typeof TABS)[number];

// PrizePicks-style bar — but NO wallet/balance/deposit chrome (research framing).
export function TopNav({
  tab,
  onTab,
  asOf,
  onRefresh,
  onToggleSidebar,
}: {
  tab: Tab;
  onTab: (t: Tab) => void;
  asOf: string;
  onRefresh: () => void;
  onToggleSidebar: () => void;
}) {
  return (
    <header className="sticky top-0 z-20 border-b border-white/5 bg-bg/85 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-[1500px] items-center gap-4 px-4 sm:px-6">
        {/* mobile sidebar toggle */}
        <button
          onClick={onToggleSidebar}
          className="flex h-9 w-9 items-center justify-center rounded-lg text-ink-dim hover:bg-surface lg:hidden"
          aria-label="Toggle utilities"
        >
          <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth={2}>
            <path d="M4 6h16M4 12h16M4 18h16" strokeLinecap="round" />
          </svg>
        </button>

        {/* brand */}
        <div className="flex items-center gap-2">
          <BoltMark className="h-7 w-7" />
          <span className="text-lg font-extrabold tracking-tight text-ink">
            prop<span className="bg-brand-bolt bg-clip-text text-transparent">-edge</span>
          </span>
        </div>

        {/* tab pill group */}
        <nav className="no-scrollbar ml-2 hidden items-center gap-1 overflow-x-auto rounded-full bg-surface/70 p-1 ring-1 ring-white/5 md:flex">
          {TABS.map((t) => (
            <button
              key={t}
              onClick={() => onTab(t)}
              className={[
                "shrink-0 rounded-full px-3.5 py-1.5 text-sm font-semibold transition",
                t === tab
                  ? "bg-violet text-white shadow-violet-soft"
                  : "text-ink-dim hover:text-ink",
              ].join(" ")}
            >
              {t}
            </button>
          ))}
        </nav>

        <div className="flex-1" />

        {/* refresh + as-of (no balance, no wallet) */}
        <div className="flex items-center gap-3">
          <span className="hidden text-[11px] leading-tight text-ink-dim lg:block">
            Showing picks as of {asOf};
            <br />
            new picks land each morning
          </span>
          <button
            onClick={onRefresh}
            className="flex items-center gap-1.5 rounded-full bg-violet px-3.5 py-2 text-sm font-semibold text-white shadow-violet-soft transition hover:brightness-110"
          >
            <RefreshIcon className="h-4 w-4" />
            <span className="hidden sm:inline">Refresh picks</span>
          </button>
        </div>
      </div>

      {/* mobile tab row */}
      <nav className="no-scrollbar flex items-center gap-1 overflow-x-auto border-t border-white/5 px-3 py-2 md:hidden">
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => onTab(t)}
            className={[
              "shrink-0 rounded-full px-3 py-1.5 text-xs font-semibold transition",
              t === tab ? "bg-violet text-white" : "bg-surface text-ink-dim",
            ].join(" ")}
          >
            {t}
          </button>
        ))}
      </nav>
    </header>
  );
}
