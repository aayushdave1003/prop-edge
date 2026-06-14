#!/usr/bin/env bash
# Point git at the tracked hooks dir so the roadmap auto-archives on commit.
# core.hooksPath is a local (per-clone) setting, so run this once after cloning.
cd "$(dirname "$0")/.."
git config core.hooksPath .githooks
chmod +x .githooks/* scripts/clean_roadmap.py 2>/dev/null || true
echo "✓ git hooks installed (core.hooksPath=.githooks) — ROADMAP auto-archives done items to CHANGELOG on commit."
