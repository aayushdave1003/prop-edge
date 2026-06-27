import { InfoIcon } from "./icons";

// HARD RULE: research framing. This text stays, verbatim in spirit, everywhere.
export function DisclaimerBanner() {
  return (
    <div className="border-b border-amber/20 bg-amber/10">
      <div className="mx-auto flex max-w-[1500px] items-start gap-2 px-4 py-2 sm:px-6">
        <InfoIcon className="mt-0.5 h-4 w-4 shrink-0 text-amber" />
        <p className="text-[12px] leading-snug text-amber/90">
          Research / paper-tracking only — not betting advice. Tracks model
          predictions against publicly-visible PrizePicks lines. Intended for 21+
          where sports wagering is legal. All results hypothetical.
        </p>
      </div>
    </div>
  );
}
