// Compliance/positioning strip — keep verbatim.
export function DisclaimerBanner() {
  return (
    <div className="border-b border-hair bg-white/[0.012]">
      <div className="mx-auto flex max-w-[1400px] items-center gap-2 px-5 py-1.5">
        <span className="h-1.5 w-1.5 rounded-full bg-warn" />
        <span className="text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
          Research · Paper-tracking only · Model leans, not betting advice
        </span>
      </div>
    </div>
  );
}
