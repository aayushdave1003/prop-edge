"""Resolution audit — does the model's probability DISCRIMINATE winners from losers,
and does any market SUBSET beat the ~0.55 single-game ceiling?

This is the rigorous answer to "is there any other way to improve the model." It is
leak-free by construction: forward-only (picked_at < game start), valid-line, settled
win/loss only. AUC is calibration-invariant, so it's the clean discrimination measure,
and its MAGNITUDE also flags leaks — a clean model on noisy single-game props lands
~0.50–0.55; anything >0.65 is a leak flag (the leaked market_odds snapshot hit 0.77).

FINDING (2026-08, n≈10.1k): NO market subset shows exploitable discrimination.
  • Pooled AUC looks like signal (~0.585) but it's a SIMPSON'S-PARADOX mirage — the
    model only "knows" that different markets have different base rates (RBI-overs hit
    ~70%, HR-unders ~85%, K-props ~50/50), which the BOOK ALREADY PRICES into the line.
  • WITHIN every (sport, stat) market, AUC collapses to ~0.50 — a coin flip — and the
    two biggest markets (RBIs n≈3.5k → 0.498, total_bases n≈2.1k → 0.512) have tight
    CIs pinned on 0.50, so a real 0.55 edge would have shown up. It doesn't.
  • The skill-driven hypothesis (pitcher strikeouts) died too: 0.480.
  Conclusion: the mean is fine, within-market resolution is ~0, and that's the
  irreducible noise of single-game props — not a model flaw a feature/algorithm fixes.

DISTRIBUTIONAL APPENDIX (--dist): the Poisson-λ assumption is CORRECT for true counts
  (hits dispersion 0.97, strikeouts 0.98 ≈ Poisson=1.0) but WRONG for total_bases
  (2.08 — a HR is 4 bases, so variance ≈ 2× mean). That over-dispersion leaks into
  upper-tail OVER-confidence (shipped p≈0.86 realizes ~0.71). A neg-binomial / proper
  TB distribution would fix tail CALIBRATION — but not resolution (see above), and the
  betting layer already Platt-calibrates, so the practical betting impact is ~nil.

Run:  python -m props.models.resolution_audit          # resolution by subset
      python -m props.models.resolution_audit --dist   # + distributional appendix
      python -m props.models.resolution_audit --selftest
"""
import argparse
import math
from collections import defaultdict

from sqlalchemy import text

from props.utils.db import engine

LEAK_FLAG = 0.65          # AUC above this on single-game props = almost certainly a leak
MIN_N = 150               # below this a subset's AUC is too noisy to conclude
DISP_STATS = ("hits", "total_bases", "strikeouts_pitcher")


def auc(rows: list[dict]) -> tuple:
    """Mann-Whitney AUC + Hanley-McNeil SE (to test AUC>0.5). rows have p, y(0/1)."""
    pos = [r["p"] for r in rows if r["y"] == 1]
    neg = [r["p"] for r in rows if r["y"] == 0]
    npos, nneg = len(pos), len(neg)
    if npos == 0 or nneg == 0:
        return None, None, npos, nneg
    merged = sorted([(p, 1) for p in pos] + [(p, 0) for p in neg])
    ranks = [0.0] * len(merged)
    i = 0
    while i < len(merged):                      # average ranks over ties
        j = i
        while j < len(merged) and merged[j][0] == merged[i][0]:
            j += 1
        avg = (i + 1 + j) / 2.0
        for k in range(i, j):
            ranks[k] = avg
        i = j
    sum_pos = sum(ranks[k] for k in range(len(merged)) if merged[k][1] == 1)
    a = (sum_pos - npos * (npos + 1) / 2.0) / (npos * nneg)
    q1, q2 = a / (2 - a), 2 * a * a / (1 + a)
    se = math.sqrt(max(0.0, (a * (1 - a) + (npos - 1) * (q1 - a * a) +
                             (nneg - 1) * (q2 - a * a)) / (npos * nneg)))
    return a, se, npos, nneg


def brier_decomp(rows: list[dict], nbins: int = 10) -> dict | None:
    """Murphy decomposition: Brier = Reliability − Resolution + Uncertainty."""
    n = len(rows)
    if n == 0:
        return None
    obar = sum(r["y"] for r in rows) / n
    brier = sum((r["p"] - r["y"]) ** 2 for r in rows) / n
    rel = res = 0.0
    for b in range(nbins):
        lo, hi = b / nbins, (b + 1) / nbins
        bin_rows = [r for r in rows if (lo <= r["p"] < hi) or (b == nbins - 1 and r["p"] == 1.0)]
        nb = len(bin_rows)
        if nb == 0:
            continue
        pbar = sum(r["p"] for r in bin_rows) / nb
        ob = sum(r["y"] for r in bin_rows) / nb
        rel += nb * (pbar - ob) ** 2
        res += nb * (ob - obar) ** 2
    rel, res = rel / n, res / n
    unc = obar * (1 - obar)
    return {"brier": brier, "rel": rel, "res": res, "unc": unc,
            "res_pct": (res / unc * 100 if unc else 0.0), "base": obar}


def load_rows() -> list[dict]:
    """Leak-free settled picks: forward-only (picked before game start), valid-line,
    win/loss only. model_prob is calibration-invariant for AUC."""
    with engine.connect() as c:
        return [dict(r) for r in c.execute(text("""
            SELECT pk.model_prob::float AS p, (pk.leg_result='win')::int AS y,
                   pk.sport_code AS sport, pk.stat_type AS stat
            FROM picks pk JOIN games g ON g.game_id = pk.game_id
            WHERE pk.leg_result IN ('win','loss') AND pk.model_prob IS NOT NULL
              AND pk.picked_at < g.game_datetime AND pk.line_id IS NOT NULL
        """)).mappings().all()]


def _line(name: str, rows: list[dict]) -> None:
    a, se, npos, nneg = auc(rows)
    n = npos + nneg
    bd = brier_decomp(rows)
    if a is None or bd is None:
        print(f"  {name:26} n={n:<5} —")
        return
    lo, hi = a - 1.96 * se, a + 1.96 * se
    flag = "  ⚠️LEAK?" if a > LEAK_FLAG else ("  ← signal?" if lo > 0.53 else
           "  (coin flip — CI incl. 0.5)")
    thin = " ·thin" if n < MIN_N else ""
    print(f"  {name:26} n={n:<5} base={bd['base']*100:4.1f}%  AUC={a:.3f} [{lo:.3f},{hi:.3f}]"
          f"  res={bd['res_pct']:4.1f}%{flag}{thin}")


def report(rows: list[dict]) -> None:
    wins = sum(r["y"] for r in rows)
    print(f"\nLeak-free settled picks: n={len(rows)} (win={wins}, base={wins/len(rows)*100:.1f}%)")
    print("AUC 0.50=coin flip · >0.65=leak flag · res=% of possible discrimination (Brier)\n")
    print("OVERALL");  _line("all picks", rows)
    print("\nBY SPORT")
    for sp in sorted({r["sport"] for r in rows}):
        _line(sp, [r for r in rows if r["sport"] == sp])
    print("\nBY SPORT · STAT  (fix the market → does within-market skill exist?)")
    groups: dict = defaultdict(list)
    for r in rows:
        groups[(r["sport"], r["stat"])].append(r)
    for key in sorted(groups, key=lambda k: -len(groups[k])):
        if len(groups[key]) >= 80:
            _line(f"{key[0]}·{key[1]}", groups[key])


def dist_report(rows: list[dict]) -> None:
    print("\n── DISTRIBUTIONAL APPENDIX ──")
    print("Angle 1 — calibration of shipped model_prob (tails = the Poisson-tail test)")
    for b in range(10):
        lo, hi = b / 10, (b + 1) / 10
        br = [r for r in rows if (lo <= r["p"] < hi) or (b == 9 and r["p"] == 1.0)]
        if not br:
            continue
        pred = sum(r["p"] for r in br) / len(br)
        act = sum(r["y"] for r in br) / len(br)
        tail = "  <-- TAIL" if b in (0, 1, 8, 9) else ""
        print(f"  [{lo:.1f},{hi:.1f})  n={len(br):5}  pred={pred:.3f}  actual={act:.3f}  gap={act-pred:+.3f}{tail}")
    print("\nAngle 2 — dispersion of ACTUALS (within-player var/mean; Poisson=1.0, >1=over-dispersed)")
    for stat in DISP_STATS:
        with engine.connect() as c:
            pg = c.execute(text("""
                SELECT pg.player_id, (pg.stats->>:s)::float AS v
                FROM player_games pg
                WHERE pg.stats ? :s AND COALESCE(pg.did_play, true)
                  AND (pg.stats->>:s) ~ '^[0-9.]+$'
            """), {"s": stat}).all()
        byp: dict = defaultdict(list)
        for pid, v in pg:
            byp[pid].append(v)
        idx = sorted(
            (sum((x - (sum(vs) / len(vs))) ** 2 for x in vs) / (len(vs) - 1)) / (sum(vs) / len(vs))
            for vs in byp.values() if len(vs) >= 20 and sum(vs) > 0)
        if idx:
            q = lambda f: idx[min(len(idx) - 1, int(f * len(idx)))]
            verdict = "≈ Poisson" if 0.85 <= q(0.5) <= 1.20 else "OVER-dispersed → Poisson mis-specified"
            print(f"  {stat:22} players≥20g={len(idx):4}  median disp={q(0.5):.2f} (IQR {q(0.25):.2f}–{q(0.75):.2f})  {verdict}")


def selftest() -> int:
    """auc() sanity — perfect separation, reversed, and ties. No DB."""
    perfect = [{"p": 0.9, "y": 1}, {"p": 0.8, "y": 1}, {"p": 0.2, "y": 0}, {"p": 0.1, "y": 0}]
    assert abs(auc(perfect)[0] - 1.0) < 1e-9, auc(perfect)[0]
    reversed_ = [{"p": 0.1, "y": 1}, {"p": 0.2, "y": 1}, {"p": 0.8, "y": 0}, {"p": 0.9, "y": 0}]
    assert abs(auc(reversed_)[0] - 0.0) < 1e-9, auc(reversed_)[0]
    ties = [{"p": 0.5, "y": 1}, {"p": 0.5, "y": 0}]        # all tied → 0.5
    assert abs(auc(ties)[0] - 0.5) < 1e-9, auc(ties)[0]
    print("selftest OK (AUC: perfect=1.0, reversed=0.0, ties=0.5)")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Leak-free resolution / discrimination audit")
    p.add_argument("--dist", action="store_true", help="also print the distributional appendix")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args()
    if args.selftest:
        raise SystemExit(selftest())
    rows = load_rows()
    report(rows)
    if args.dist:
        dist_report(rows)


if __name__ == "__main__":
    main()
