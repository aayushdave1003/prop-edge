"""Automated retrain + A/B-gated promote — closes the retrain loop.

For each registered model this does exactly what the weather retrain did by hand,
but safely and unattended:

  1. back up the live (prod) model + meta,
  2. retrain a CANDIDATE (the training module overwrites the prod files),
  3. set the candidate aside and RESTORE prod,
  4. score candidate vs prod on recent settled games (`ab_compare.compare`),
  5. PROMOTE only if the candidate beats prod by >= MIN_PROMOTE_PCT MAE —
     then recalibrate it; otherwise keep prod untouched.

Nothing is promoted on a regression (the home_runs+weather case stayed prod at
-7.7%). Each decision is logged to `backtest_runs` (trigger `autoretrain:<stat>`)
so it shows on the Performance tab, and a Discord summary fires at the end.

The weekly GHA job (`weekly_retrain.yml`) runs this and commits whatever model
files changed, so promotions deploy themselves.

Run:  python -m props.models.retrain_and_promote                 # all models
      python -m props.models.retrain_and_promote --only total_bases hits
      python -m props.models.retrain_and_promote --min-improve 1.0 --days 90
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from sqlalchemy import text

from props.models.ab_compare import STAT_MODEL, compare
from props.utils.db import engine
from props.utils.config import settings
from props.utils.logging import log, configure_logging

MODEL_DIR = Path("models")
MIN_PROMOTE_PCT = 0.5     # candidate must beat prod by >=0.5% MAE to promote
AB_DAYS = 60              # settled-game window for the A/B score

# stat -> training module run as `python -m <module>` (overwrites models/<name>.*)
TRAIN_MODULE = {
    "total_bases": "props.models.total_bases_v1",
    "hits":        "props.models.hits_v1",
    "home_runs":   "props.models.mlb_home_runs_v1",
}


def _recalibrate(stat: str):
    """Refit the isotonic calibrator for a freshly-promoted model."""
    name = STAT_MODEL[stat]
    if name == "hits_v1":   # has its own bespoke calibration script
        subprocess.run([sys.executable, "-m", "props.models.calibrate_hits_v1"],
                       check=True)
        return
    from props.models.calibrate_models import CONFIGS, calibrate_one
    cfg = next((c for c in CONFIGS if c["name"] == name), None)
    if cfg:
        print("  recal:", calibrate_one(cfg, force=True))
    else:
        log.warning("no_calibrator_config", model=name)


def _log_decision(stat: str, n: int, improvement: float, promoted: bool):
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO backtest_runs
                    (run_at, sport, n_picks, mae_improvement_pct, trigger)
                VALUES (NOW(), 'mlb', :n, :imp, :trig)
            """), {"n": n, "imp": round(improvement, 2),
                   "trig": f"autoretrain:{stat}" + ("" if promoted else ":kept")})
    except Exception as e:
        log.warning("autoretrain_log_failed", error=str(e)[:120])


def retrain_one(stat: str, min_improve: float, days: int) -> dict:
    """Retrain one model, A/B-gate it, promote only if it wins. Returns a result
    dict; leaves prod untouched on any failure or regression."""
    name = STAT_MODEL[stat]
    prod_txt, prod_meta = MODEL_DIR / f"{name}.txt", MODEL_DIR / f"{name}_meta.json"
    bak_txt, bak_meta = Path(f"/tmp/{name}.bak.txt"), Path(f"/tmp/{name}.bak_meta.json")
    cand_txt = Path(f"/tmp/{name}_cand.txt")
    cand_meta = Path(f"/tmp/{name}_cand_meta.json")   # _predict swaps .txt->_meta.json

    if not prod_txt.exists():
        return {"stat": stat, "status": "skip", "detail": "no prod model"}

    shutil.copy(prod_txt, bak_txt)
    shutil.copy(prod_meta, bak_meta)
    try:
        log.info("retrain_start", stat=stat, model=name)
        subprocess.run([sys.executable, "-m", TRAIN_MODULE[stat]], check=True)
        # training overwrote prod with the candidate — set it aside, restore prod
        shutil.copy(prod_txt, cand_txt)
        shutil.copy(prod_meta, cand_meta)
        shutil.copy(bak_txt, prod_txt)
        shutil.copy(bak_meta, prod_meta)

        res = compare(stat, str(cand_txt), days)
        if res is None:
            return {"stat": stat, "status": "no_data"}

        imp = res["improvement_pct"]
        promote = imp >= min_improve
        if promote:
            shutil.copy(cand_txt, prod_txt)
            shutil.copy(cand_meta, prod_meta)
            _recalibrate(stat)
        _log_decision(stat, res["n"], imp, promote)
        log.info("retrain_decision", stat=stat, improvement_pct=round(imp, 2),
                 n=res["n"], promoted=promote)
        return {"stat": stat, "status": "promoted" if promote else "kept",
                "improvement_pct": imp, "n": res["n"],
                "mae_prod": res["mae_prod"], "mae_cand": res["mae_cand"]}
    except Exception as e:
        # any failure: make sure prod is whole again
        shutil.copy(bak_txt, prod_txt)
        shutil.copy(bak_meta, prod_meta)
        log.warning("retrain_failed", stat=stat, error=str(e)[:200])
        return {"stat": stat, "status": "error", "detail": str(e)[:200]}


def _notify(results: list[dict]):
    if not settings.discord_webhook_url:
        return
    promoted = [r for r in results if r["status"] == "promoted"]
    icon = {"promoted": "✅", "kept": "•", "no_data": "∅", "skip": "∅", "error": "⚠️"}
    lines = []
    for r in results:
        if "improvement_pct" in r:
            lines.append(f"{icon.get(r['status'],'•')} **{r['stat']}** "
                         f"{r['improvement_pct']:+.1f}% MAE → {r['status']}")
        else:
            lines.append(f"{icon.get(r['status'],'•')} **{r['stat']}** — {r['status']}"
                         + (f" ({r.get('detail','')})" if r.get('detail') else ""))
    import requests
    payload = {"embeds": [{
        "title": f"🔁 weekly retrain — {len(promoted)} promoted",
        "description": "\n".join(lines)
        + "\n\n_Promoted models deploy on the next commit; rest kept prod._",
        "color": 0x2ECC71 if promoted else 0x95A5A6,
        "footer": {"text": "retrain_and_promote"},
    }]}
    try:
        requests.post(settings.discord_webhook_url, json=payload, timeout=10)
    except Exception as e:
        log.warning("retrain_notify_failed", error=str(e)[:120])


def main():
    configure_logging()
    p = argparse.ArgumentParser()
    p.add_argument("--only", nargs="*", choices=list(TRAIN_MODULE),
                   help="subset of stats to retrain (default: all)")
    p.add_argument("--min-improve", type=float, default=MIN_PROMOTE_PCT,
                   help="min %% MAE improvement to promote (default 0.5)")
    p.add_argument("--days", type=int, default=AB_DAYS)
    p.add_argument("--no-notify", action="store_true")
    args = p.parse_args()

    stats = args.only or list(TRAIN_MODULE)
    print(f"=== Auto-retrain ({', '.join(stats)}; gate ≥{args.min_improve}% MAE) ===")
    results = [retrain_one(s, args.min_improve, args.days) for s in stats]

    print("\n=== Summary ===")
    for r in results:
        if "improvement_pct" in r:
            print(f"  {r['stat']:<12} {r['improvement_pct']:+6.2f}% MAE  "
                  f"(prod {r['mae_prod']:.4f} → cand {r['mae_cand']:.4f}, "
                  f"n={r['n']})  → {r['status'].upper()}")
        else:
            print(f"  {r['stat']:<12} {r['status'].upper()}"
                  + (f" — {r.get('detail','')}" if r.get('detail') else ""))

    if not args.no_notify:
        _notify(results)
    promoted = [r for r in results if r["status"] == "promoted"]
    print(f"\n{len(promoted)} promoted, {len(results) - len(promoted)} kept prod.")
    return results


if __name__ == "__main__":
    main()
