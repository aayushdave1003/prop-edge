#!/usr/bin/env python3
"""Keep ROADMAP.md showing only OPEN work — auto-archive finished items.

Moves every done bullet (a top-level list item starting with `- ✅` or `- ☑`)
out of ROADMAP.md into CHANGELOG.md, and drops any section header left with no
remaining items. Idempotent + a no-op when there's nothing done to move, so it's
safe to run on every commit (see .githooks/pre-commit).

Run:  python scripts/clean_roadmap.py
"""
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROADMAP = ROOT / "ROADMAP.md"
CHANGELOG = ROOT / "CHANGELOG.md"

DONE_RE = re.compile(r"^- (✅|☑)")          # a completed bullet
HEADER_RE = re.compile(r"^#{2,4}\s")        # a section header (##, ###, ####)
# A header is a "section" we may drop if it ends up empty; the top title (#) and
# prose lines are always kept.


def _is_content(line: str) -> bool:
    """A non-blank, non-header line counts as section content worth keeping."""
    return bool(line.strip()) and not HEADER_RE.match(line)


def clean() -> int:
    if not ROADMAP.exists():
        return 0
    lines = ROADMAP.read_text().splitlines()

    done = [ln for ln in lines if DONE_RE.match(ln)]
    if not done:
        return 0                                   # nothing to archive

    kept = [ln for ln in lines if not DONE_RE.match(ln)]

    # Drop section headers that no longer have any content before the next header.
    out: list[str] = []
    i = 0
    while i < len(kept):
        line = kept[i]
        if HEADER_RE.match(line):
            j = i + 1
            has_content = False
            while j < len(kept) and not HEADER_RE.match(kept[j]):
                if _is_content(kept[j]):
                    has_content = True
                j += 1
            if not has_content:                    # empty section → skip header
                i = j
                continue
        out.append(line)
        i += 1

    # Collapse 3+ blank lines left by removals down to one.
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).rstrip() + "\n"
    ROADMAP.write_text(text)

    # Append the archived items to CHANGELOG under a dated heading.
    header = f"## Shipped — {date.today().isoformat()}\n"
    block = header + "\n".join(done) + "\n\n"
    if CHANGELOG.exists():
        existing = CHANGELOG.read_text()
    else:
        existing = "# Changelog\n\nAuto-archived from ROADMAP.md as items ship.\n\n"
    # Merge into an existing same-day heading if present, else prepend the block
    # above older entries (newest first, under the intro).
    if header in existing:
        existing = existing.replace(header, header + "\n".join(done) + "\n")
    else:
        parts = existing.split("\n\n", 2)          # [title, intro, rest]
        if len(parts) == 3:
            existing = parts[0] + "\n\n" + parts[1] + "\n\n" + block + parts[2]
        else:
            existing = existing.rstrip() + "\n\n" + block
    CHANGELOG.write_text(existing)
    return len(done)


if __name__ == "__main__":
    n = clean()
    if n:
        print(f"clean_roadmap: archived {n} done item(s) to CHANGELOG.md")
    sys.exit(0)
