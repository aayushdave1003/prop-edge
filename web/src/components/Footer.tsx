// Utility footer links in the blue/amber utility style. Research framing — these
// are informational, not a betting product's help chrome.
export function Footer() {
  return (
    <footer className="mt-14 flex flex-col items-center gap-2">
      <div className="flex items-center gap-3 text-[11px] font-bold uppercase tracking-wide">
        <a href="#" className="text-util-blue underline underline-offset-2 hover:brightness-125">
          Help Center
        </a>
        <span className="h-3 w-px bg-white/15" />
        <a href="#" className="text-util-blue underline underline-offset-2 hover:brightness-125">
          How It Works
        </a>
        <span className="h-3 w-px bg-white/15" />
        <a href="#" className="text-util-blue underline underline-offset-2 hover:brightness-125">
          Scoring Chart
        </a>
      </div>
      <p className="text-[11px] text-amber/80">
        Research / paper-tracking only — not betting advice. All results hypothetical.
      </p>
    </footer>
  );
}
