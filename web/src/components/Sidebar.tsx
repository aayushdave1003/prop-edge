// Left utility rail (research equivalents of PrizePicks' account menu — no wallet).
// Every item is wired to a real in-app action. Collapsible on mobile via `open`.

export interface SidebarActions {
  theme: "dark" | "light";
  watchedOnly: boolean;
  watchCount: number;
  onToggleTheme: () => void;
  onPlayerLookup: () => void;
  onToggleWatchlist: () => void;
  onShare: () => void;
  onOps: () => void;
  onPickHistory: () => void;
}

const ICONS = {
  light: "M12 3v2M12 19v2M5 12H3M21 12h-2M6.3 6.3 4.9 4.9M19.1 19.1l-1.4-1.4M17.7 6.3l1.4-1.4M4.9 19.1l1.4-1.4",
  lookup: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14ZM20 20l-3-3",
  star: "M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 17.9 6.8 19.6l1-5.8L3.5 9.7l5.9-.9L12 3.5Z",
  share: "M4 12v7a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-7M16 6l-4-4-4 4M12 2v13",
  ops: "M3 3v18h18M7 15l3-4 3 3 4-6",
  history: "M12 7v5l3 2M21 12a9 9 0 1 1-9-9",
};

export function Sidebar({
  open,
  onClose,
  actions,
}: {
  open: boolean;
  onClose: () => void;
  actions: SidebarActions;
}) {
  const items = [
    { label: actions.theme === "dark" ? "Light mode" : "Dark mode", icon: ICONS.light, onClick: actions.onToggleTheme, active: false },
    { label: "Player lookup", icon: ICONS.lookup, onClick: actions.onPlayerLookup, active: false },
    {
      label: actions.watchCount ? `Watchlist · ${actions.watchCount}` : "Watchlist",
      icon: ICONS.star,
      onClick: actions.onToggleWatchlist,
      active: actions.watchedOnly,
    },
    { label: "Share results", icon: ICONS.share, onClick: actions.onShare, active: false },
    { label: "Ops · cost & usage", icon: ICONS.ops, onClick: actions.onOps, active: false },
    { label: "Pick history", icon: ICONS.history, onClick: actions.onPickHistory, active: false },
  ];

  return (
    <>
      {open && (
        <div onClick={onClose} className="fixed inset-0 z-30 bg-black/50 backdrop-blur-sm lg:hidden" />
      )}
      <aside
        className={[
          "z-40 w-56 shrink-0 border-r border-white/5 bg-surface/40",
          "fixed inset-y-0 left-0 transition-transform lg:static lg:translate-x-0",
          open ? "translate-x-0" : "-translate-x-full",
        ].join(" ")}
      >
        <div className="p-3">
          <p className="px-2 pb-2 text-[11px] font-semibold uppercase tracking-wide text-ink-dim">
            Utilities
          </p>
          <nav className="space-y-0.5">
            {items.map((it) => (
              <button
                key={it.label}
                onClick={() => {
                  it.onClick();
                  onClose();
                }}
                className={[
                  "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm transition",
                  it.active
                    ? "bg-violet/15 text-violet"
                    : "text-ink-dim hover:bg-surface-hover hover:text-ink",
                ].join(" ")}
              >
                <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
                  <path d={it.icon} />
                </svg>
                {it.label}
              </button>
            ))}
          </nav>
        </div>
      </aside>
    </>
  );
}
