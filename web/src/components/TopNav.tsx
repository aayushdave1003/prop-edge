import { ChevronDown, LogoMark, PlusIcon } from "./icons";

// Sportsbook-style top bar. The account/balance/invite cluster is styled but
// non-functional (placeholders), per the brief.
export function TopNav() {
  return (
    <header className="sticky top-0 z-20 border-b border-white/5 bg-bg/85 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-[1400px] items-center gap-6 px-4 sm:px-6">
        {/* logo */}
        <div className="flex items-center gap-2">
          <LogoMark className="h-7 w-7 text-brand" />
          <span className="text-lg font-extrabold tracking-tight text-ink">
            prop<span className="text-brand">-edge</span>
          </span>
        </div>

        {/* primary tabs */}
        <nav className="hidden items-center gap-1 sm:flex">
          <Tab label="Board" active />
          <Tab label="My Lineups" />
        </nav>

        <div className="flex-1" />

        {/* right cluster */}
        <div className="flex items-center gap-2 sm:gap-3">
          <button className="hidden items-center gap-2 rounded-full px-2 py-1 text-sm font-semibold text-ink-dim transition hover:text-ink md:flex">
            Invite Friends
            <span className="rounded-full bg-edge px-2 py-0.5 text-xs font-bold text-black">
              GET $25
            </span>
          </button>
          <span className="hidden rounded-full bg-surface px-3 py-1.5 text-sm font-bold text-ink ring-1 ring-white/8 sm:inline">
            $0.00
          </span>
          <button
            className="flex h-9 w-9 items-center justify-center rounded-full bg-brand text-black shadow-glow-sm transition hover:brightness-110"
            aria-label="Add funds"
          >
            <PlusIcon className="h-4 w-4" />
          </button>
          <button className="flex items-center gap-1 rounded-full bg-surface py-1 pl-1 pr-2 ring-1 ring-white/8">
            <span className="flex h-7 w-7 items-center justify-center rounded-full bg-gradient-to-br from-brand/30 to-surface-hover text-xs font-bold text-ink">
              AD
            </span>
            <ChevronDown className="h-3.5 w-3.5 text-ink-dim" />
          </button>
        </div>
      </div>
    </header>
  );
}

function Tab({ label, active }: { label: string; active?: boolean }) {
  return (
    <button
      className={[
        "relative px-3 py-2 text-sm font-semibold transition",
        active ? "text-ink" : "text-ink-dim hover:text-ink",
      ].join(" ")}
    >
      {label}
      {active && (
        <span className="absolute inset-x-2 -bottom-[1px] h-0.5 rounded-full bg-brand shadow-glow-sm" />
      )}
    </button>
  );
}
