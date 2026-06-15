"""prop-edge dashboard — PrizePicks-style UI.

Run: streamlit run ui/dashboard.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import pandas as pd
from sqlalchemy import text
from props.utils.db import engine
from props.maintenance.migrate import run_migrations
from props.models.category_cutoffs import rec_cutoff, load_cutoffs, compute_from_db
from props.models.prob_calibration import calibrate
from props.picks.build_parlays import build_diversified_parlay
from props.picks.compute_clv import clv_points

# Apply any pending schema migrations on startup (idempotent, tracked).
run_migrations()


@st.cache_resource
def _verify_native_deps() -> str:
    """Import LightGBM once per container so a libgomp/native-lib regression is
    visible in the deploy logs immediately — it has broken prod before (Nixpacks'
    linker couldn't see apt's libgomp.so.1). Fail-loud beats discovering it only
    when someone opens a tab that runs inference.
    """
    try:
        import lightgbm as lgb
        print(f"[startup] native deps OK — lightgbm {lgb.__version__} "
              "(libgomp resolved)", flush=True)
        return f"ok {lgb.__version__}"
    except Exception as e:  # pragma: no cover - environment-specific
        print(f"[startup] NATIVE DEP FAILURE — lightgbm import failed: {e}", flush=True)
        return f"failed: {e}"


_verify_native_deps()

# "Recommended" picks are now tuned PER CATEGORY, not by one global threshold.
# A flat cutoff is wrong in both directions at once: the MLB model clears the
# 2-pick breakeven (57.7%) even at the pick-generation floor, while the NBA
# model is a coin-flip until very high confidence. Cutoffs are recomputed live
# from settled history every 6h (committed category_cutoffs.json is the
# instant seed/fallback). Recompute the file offline: python -m
# props.models.category_cutoffs.


@st.cache_data(ttl=21600)  # 6h: cutoffs only move as new picks settle
def _cutoff_table() -> dict:
    """Live per-category cutoffs from the DB, falling back to the seed JSON."""
    try:
        return compute_from_db(engine)
    except Exception:
        return load_cutoffs()


CUTOFFS = _cutoff_table()
DEFAULT_CUTOFF = float(CUTOFFS.get("default_cutoff", 0.70))


def _rec_mask(df: pd.DataFrame) -> pd.Series:
    """Boolean mask: each pick meets its category's recommended cutoff."""
    if df.empty:
        return pd.Series([], dtype=bool)
    return df.apply(
        lambda r: float(r["model_prob"]) >= rec_cutoff(
            r.get("sport_code"), r.get("stat_type"), table=CUTOFFS,
            direction=r.get("direction")),
        axis=1,
    )


_STACK_STAT = {"hits": "hits", "total_bases": "TB", "rbis": "RBI", "home_runs": "HR"}


def correlated_stacks(df: pd.DataFrame) -> list[dict]:
    """Same-game positively-correlated stacks: a pitcher's strikeouts OVER paired
    with an opposing-team batter UNDER — when the pitcher deals, the opposing
    offense falls short, so the legs tend to hit (or miss) together. Returns the
    pairs sorted by (correlation-bumped) joint probability."""
    if df.empty or "game_id" not in df.columns:
        return []
    pitch = df[(df["stat_type"] == "strikeouts_pitcher") & (df["direction"] == "over")]
    unders = df[(df["direction"] == "under") & (df["stat_type"].isin(_STACK_STAT))]
    out = []
    for _, p in pitch.iterrows():
        opp = unders[(unders["game_id"] == p["game_id"])
                     & (unders["team"].astype(str) != str(p.get("team")))]
        for _, b in opp.iterrows():
            joint = min(0.99, calibrate(float(p["model_prob"]))
                        * calibrate(float(b["model_prob"])) + 0.06)   # +corr bump
            out.append({"pitcher": p["player"], "p_line": float(p["line"]),
                        "batter": b["player"], "b_line": float(b["line"]),
                        "b_stat": _STACK_STAT.get(b["stat_type"], b["stat_type"]),
                        "joint": joint})
    out.sort(key=lambda x: -x["joint"])
    return out

st.set_page_config(page_title="prop-edge", layout="wide",
                   initial_sidebar_state="collapsed",
                   page_icon="⚡")

# ── Mobile / "add to home screen" (PWA-lite) ─────────────────────────────────
# Inject iOS/Android home-screen meta into the parent <head> so the dashboard
# can be saved as a standalone app icon. (Full offline PWA needs a served
# manifest + service worker; this is the achievable subset in Streamlit.)
try:
    import streamlit.components.v1 as _components
    _components.html("""<script>
      const head = window.parent.document.head;
      const add = (name, content) => {
        if (window.parent.document.querySelector(`meta[name="${name}"]`)) return;
        const m = window.parent.document.createElement('meta');
        m.setAttribute('name', name); m.setAttribute('content', content); head.appendChild(m);
      };
      add('apple-mobile-web-app-capable', 'yes');
      add('mobile-web-app-capable', 'yes');
      add('apple-mobile-web-app-status-bar-style', 'black-translucent');
      add('apple-mobile-web-app-title', 'prop-edge');
      add('theme-color', '#0a0b10');
    </script>""", height=0)
except Exception:
    pass

# ── CSS ─────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');

/* ── Design tokens ─────────────────────────────────────────────
   --bg deep base · --surface glass card · --line hairline border
   --txt/--txt2/--txt3 text ramp · --acc purple accent · over/under */
:root {
    --bg:#0a0b10; --surface:#15171f; --surface2:#1b1e28;
    --line:rgba(255,255,255,0.07); --line2:rgba(255,255,255,0.12);
    --txt:#f3f5fb; --txt2:#9aa3b8; --txt3:#5f6678;
    --acc:#7c5cff; --acc2:#9d7bff;
    --over:#2ee6a6; --under:#ff5d6c; --gold:#ffcf5c;
}

/* Base */
html, body, [class*="css"] {
    font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
}
[data-testid="stAppViewContainer"] {
    background:
      radial-gradient(900px 500px at 12% -8%, rgba(124,92,255,0.16), transparent 60%),
      radial-gradient(800px 500px at 95% 0%, rgba(34,211,238,0.08), transparent 55%),
      var(--bg);
}
[data-testid="stHeader"] { background: transparent; }
section[data-testid="stSidebar"] { background:#0e1016; }
.block-container { padding-top: 1.4rem !important; max-width: 1280px; }

/* Typography */
h1,h2,h3 { color:var(--txt) !important; letter-spacing:-0.02em; font-weight:800; }
p, label, div { color:var(--txt2); }
::selection { background: rgba(124,92,255,0.35); }

/* Metric cards */
[data-testid="stMetric"], [data-testid="metric-container"] {
    background: linear-gradient(180deg, rgba(255,255,255,0.04), rgba(255,255,255,0.015));
    border:1px solid var(--line); border-radius:14px;
    padding:14px 18px; position:relative; overflow:hidden;
}
[data-testid="stMetric"]::before {
    content:""; position:absolute; left:0; top:0; bottom:0; width:3px;
    background:linear-gradient(180deg,var(--acc),var(--acc2));
}
[data-testid="stMetricValue"] {
    color:var(--txt) !important; font-size:1.7rem !important;
    font-weight:800 !important; letter-spacing:-0.02em;
}
[data-testid="stMetricLabel"] {
    color:var(--txt3) !important; text-transform:uppercase;
    letter-spacing:0.06em; font-size:0.7rem !important;
}

/* Tabs */
.stTabs [data-baseweb="tab-list"] {
    background:rgba(255,255,255,0.03); border:1px solid var(--line);
    border-radius:14px; padding:5px; gap:4px;
}
.stTabs [data-baseweb="tab"] {
    color:var(--txt2); border-radius:10px; padding:9px 22px;
    font-weight:600; font-size:0.9rem; transition:all 0.18s ease;
}
.stTabs [data-baseweb="tab"]:hover { color:var(--txt); background:rgba(255,255,255,0.04); }
.stTabs [data-baseweb="tab"][aria-selected="true"] {
    background:linear-gradient(135deg,var(--acc),var(--acc2)) !important;
    color:#fff !important; box-shadow:0 4px 16px rgba(124,92,255,0.4);
}
.stTabs [data-baseweb="tab-highlight"] { background:transparent !important; }

/* Pick cards */
/* Responsive card grid: as many ~300px columns as fit (≈3-4 on desktop),
   auto-stacking to a single column on phones — no fixed per-row count. */
.card-grid {
    display:grid;
    grid-template-columns:repeat(auto-fill, minmax(300px, 1fr));
    gap:18px;
    align-items:start;
}
@media (max-width:520px){
    .card-grid { grid-template-columns:1fr; gap:12px; }
}
.pick-card {
    background:linear-gradient(180deg, var(--surface2), var(--surface));
    border:1px solid var(--line);
    border-radius:18px;
    padding:0; margin-bottom:0;
    overflow:hidden; min-height:344px;
    display:flex; flex-direction:column;
    position:relative;
    transition:transform .2s cubic-bezier(.2,.8,.2,1), box-shadow .2s ease, border-color .2s ease;
}
.pick-card:hover {
    transform:translateY(-4px);
    border-color:var(--line2);
    box-shadow:0 18px 40px rgba(0,0,0,0.45), 0 0 0 1px rgba(124,92,255,0.18);
}
.pick-card.rec {
    border-color:rgba(255,207,92,0.45);
    box-shadow:0 0 0 1px rgba(255,207,92,0.25), 0 8px 28px rgba(255,207,92,0.06);
}
.pick-card.rec:hover {
    box-shadow:0 18px 40px rgba(0,0,0,0.45), 0 0 0 1px rgba(255,207,92,0.5);
}
.rec-star { filter:drop-shadow(0 0 4px rgba(255,207,92,0.6)); }
.wx-chip { display:inline-block; margin-top:5px; padding:2px 8px; border-radius:999px;
           font-size:0.72rem; font-weight:600; background:rgba(255,255,255,0.06);
           color:#9aa0b4; border:1px solid var(--line); }
.wx-chip.wx-out { background:rgba(0,212,160,0.12); color:#00d4a0; border-color:rgba(0,212,160,0.3); }
.wx-chip.wx-in  { background:rgba(232,99,99,0.10); color:#e86363; border-color:rgba(232,99,99,0.25); }
.proj-line { font-size:0.76rem; color:var(--txt2); margin-top:6px; }
.proj-line b { color:var(--txt); }
.card-banner {
    position:relative; height:112px; overflow:hidden;
    background:
      radial-gradient(120px 120px at 78% 30%, rgba(124,92,255,0.22), transparent 70%),
      linear-gradient(135deg,#20243a 0%,#171a28 100%);
    border-bottom:1px solid var(--line);
}
.card-banner .team-logo {
    position:absolute; top:12px; right:12px;
    width:42px; height:42px; object-fit:contain;
    opacity:0.95; filter:drop-shadow(0 2px 6px rgba(0,0,0,0.5));
}
.card-banner .player-photo {
    position:absolute; bottom:0; left:14px;
    height:108px; width:auto; object-fit:cover; object-position:top;
    border-radius:10px 10px 0 0;
    -webkit-mask-image:linear-gradient(180deg,#000 78%,transparent);
            mask-image:linear-gradient(180deg,#000 78%,transparent);
}
.card-body { padding:13px 15px 15px; flex:1; }
.player-name {
    font-size:1rem; font-weight:700; color:var(--txt);
    margin-bottom:2px; letter-spacing:-0.01em;
    white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}
.team-stat {
    font-size:0.72rem; color:var(--txt3); margin-bottom:12px;
    text-transform:uppercase; letter-spacing:0.04em; font-weight:500;
}
.line-row { display:flex; align-items:center; justify-content:space-between; margin-bottom:10px; }
.line-value {
    font-size:1.75rem; font-weight:900; letter-spacing:-0.03em;
    background:linear-gradient(180deg,#ffffff,#c7ccdb);
    -webkit-background-clip:text; -webkit-text-fill-color:transparent; background-clip:text;
}
.badge {
    font-size:0.68rem; font-weight:800; letter-spacing:0.1em;
    padding:5px 12px; border-radius:8px; border:1px solid transparent;
}
.badge.over  {
    color:#0a0b10; background:linear-gradient(135deg,#3df0b2,#16c98c);
    box-shadow:0 3px 12px rgba(46,230,166,0.3);
}
.badge.under {
    color:#0a0b10; background:linear-gradient(135deg,#ff7b86,#ff4d5e);
    box-shadow:0 3px 12px rgba(255,93,108,0.3);
}
.prob-row { display:flex; align-items:center; justify-content:space-between; margin-bottom:10px; }
.prob-label { font-size:0.72rem; color:var(--txt3); }
.prob-value { font-size:0.9rem; font-weight:700; color:var(--txt); }
.edge-bar-bg {
    height:5px; background:rgba(255,255,255,0.06); border-radius:3px;
    margin-bottom:12px; overflow:hidden;
}
.edge-bar-fill {
    height:5px; border-radius:3px;
    background:linear-gradient(90deg,var(--acc),var(--acc2),#22d3ee);
    box-shadow:0 0 12px rgba(124,92,255,0.5);
}
.form-section { margin-top:10px; }
.form-label {
    font-size:0.66rem; color:var(--txt3); margin-bottom:6px;
    text-transform:uppercase; letter-spacing:0.05em;
}
.form-dots { display:flex; gap:5px; align-items:center; margin-bottom:5px; }
.dot {
    width:22px; height:22px; border-radius:7px;
    display:flex; align-items:center; justify-content:center;
    font-size:0.64rem; font-weight:800;
}
.dot.hit   { background:linear-gradient(135deg,rgba(46,230,166,0.28),rgba(46,230,166,0.12)); color:var(--over); border:1px solid rgba(46,230,166,0.5); }
.dot.miss  { background:linear-gradient(135deg,rgba(255,93,108,0.26),rgba(255,93,108,0.1)); color:var(--under); border:1px solid rgba(255,93,108,0.45); }
.dot.empty { background:rgba(255,255,255,0.04); color:var(--txt3); border:1px solid var(--line); }
.form-rate { font-size:0.72rem; color:var(--txt3); }
.why {
    font-size:0.72rem; color:var(--txt2); margin-top:10px;
    padding:7px 10px; border-radius:8px;
    background:rgba(124,92,232,0.10); border:1px solid rgba(124,92,232,0.22);
    line-height:1.3;
}
.form-rate span { color:var(--txt); font-weight:700; }
.inj-badge {
    font-size:0.66rem; color:var(--gold); background:rgba(255,207,92,0.1);
    border:1px solid rgba(255,207,92,0.28); border-radius:7px;
    padding:3px 8px; margin-top:8px; display:inline-block;
}
.kelly-row { font-size:0.7rem; color:var(--txt3); margin-top:4px; }
.kelly-row span { color:var(--acc2); font-weight:700; }
.inj-status {
    font-size:0.7rem; font-weight:700; border-radius:7px;
    padding:4px 9px; margin:6px 0 2px; display:block;
}
.inj-status.out  { color:#ff5d6c; background:rgba(255,93,108,0.12); border:1px solid rgba(255,93,108,0.4); }
.inj-status.warn { color:var(--gold); background:rgba(255,207,92,0.1); border:1px solid rgba(255,207,92,0.3); }
.inj-status .inj-note { font-weight:400; color:var(--txt3); }

/* Slate card */
.slate-card {
    position:relative;
    background:
      radial-gradient(600px 200px at 0% 0%, rgba(124,92,255,0.16), transparent 60%),
      linear-gradient(180deg, var(--surface2), var(--surface));
    border:1px solid rgba(124,92,255,0.4);
    border-radius:18px; padding:22px 26px; margin-bottom:26px;
    box-shadow:0 12px 36px rgba(124,92,255,0.14);
}
.slate-title { font-size:1.15rem; font-weight:800; color:#fff; margin-bottom:4px; letter-spacing:-0.01em; }
.slate-meta  { font-size:0.76rem; color:var(--txt3); margin-bottom:16px; }
.slate-leg {
    display:flex; align-items:center; justify-content:space-between;
    padding:11px 0; border-bottom:1px solid var(--line);
}
.slate-leg:last-child { border-bottom:none; }
.leg-player { font-size:0.88rem; font-weight:600; color:#fff; }
.leg-detail { font-size:0.76rem; color:var(--txt3); margin-top:2px; }
.leg-badge  { font-size:0.66rem; padding:3px 10px; border-radius:8px; }

/* Game prediction card */
.game-card {
    background:linear-gradient(180deg, var(--surface2), var(--surface));
    border:1px solid var(--line);
    border-radius:18px; padding:22px; margin-bottom:18px;
    transition:transform .2s ease, border-color .2s ease;
}
.game-card:hover { transform:translateY(-3px); border-color:var(--line2); }
.game-teams { font-size:1.05rem; font-weight:700; color:#fff; margin-bottom:14px; letter-spacing:-0.01em; }
.win-bar-bg {
    height:9px; background:rgba(255,255,255,0.06); border-radius:5px;
    margin:8px 0; overflow:hidden; display:flex; gap:2px;
}
.win-bar-home { height:9px; background:linear-gradient(90deg,var(--acc),var(--acc2)); border-radius:5px 0 0 5px; }
.win-bar-away { height:9px; background:rgba(255,255,255,0.16); border-radius:0 5px 5px 0; }
.team-prob { display:flex; justify-content:space-between; font-size:0.82rem; }
.team-prob .fav { color:var(--txt); font-weight:700; }
.team-prob .dog { color:var(--txt3); }
.market-row {
    display:flex; justify-content:space-between; align-items:center;
    margin-top:14px; padding-top:14px; border-top:1px solid var(--line);
    font-size:0.8rem;
}
.rec-strong { color:var(--over); font-weight:700; }
.rec-lean   { color:var(--gold); font-weight:700; }
.rec-pass   { color:var(--txt3); }

/* Streamlit chrome polish */
[data-testid="stDataFrame"] { border:1px solid var(--line); border-radius:12px; overflow:hidden; }
div[data-baseweb="select"] > div { background:rgba(255,255,255,0.03); border-color:var(--line); border-radius:10px; }
hr { border-color:var(--line) !important; }

/* Mobile / narrow screens: stack columns full-width instead of cramming 3-up */
@media (max-width: 640px) {
    .block-container { padding-left:0.6rem !important; padding-right:0.6rem !important; }
    [data-testid="stHorizontalBlock"] { flex-wrap:wrap !important; gap:8px !important; }
    [data-testid="stHorizontalBlock"] > [data-testid="column"],
    [data-testid="stColumn"] { flex:1 1 100% !important; min-width:100% !important; }
    [data-testid="stMetricValue"] { font-size:1.3rem !important; }
    .pick-card { min-height:0; }
    .stTabs [data-baseweb="tab"] { padding:8px 12px; font-size:0.82rem; }
}
</style>
""", unsafe_allow_html=True)


# ── Helpers ──────────────────────────────────────────────────────────────────

FALLBACK_PHOTO = "https://cdn.nba.com/headshots/nba/latest/1040x760/fallback.png"

def player_photo_url(external_id: str, sport: str) -> str:
    if not external_id or external_id.startswith("pp_"):
        return FALLBACK_PHOTO
    if sport == "nba":
        return f"https://cdn.nba.com/headshots/nba/latest/1040x760/{external_id}.png"
    if sport == "mlb":
        return (f"https://img.mlbstatic.com/mlb-photos/image/upload/"
                f"d_people:generic:headshot:67:current.png/w_213,q_auto:best"
                f"/v1/people/{external_id}/headshot/67/current")
    if sport == "wnba":
        return f"https://a.espncdn.com/combiner/i?img=/i/headshots/wnba/players/full/{external_id}.png&w=350&h=254"
    if sport == "nhl":
        return f"https://assets.nhle.com/mugs/nhl/20242025/{external_id}.png"
    return FALLBACK_PHOTO


def team_logo_url(team_ext_id: str, sport: str) -> str:
    if not team_ext_id:
        return ""
    if sport == "nba":
        return f"https://cdn.nba.com/logos/nba/{team_ext_id}/global/L/logo.svg"
    if sport == "mlb":
        return f"https://www.mlbstatic.com/team-logos/{team_ext_id}.svg"
    if sport == "wnba":
        return f"https://a.espncdn.com/i/teamlogos/wnba/500/{team_ext_id}.png"
    if sport == "nhl":
        return f"https://assets.nhle.com/logos/nhl/svg/{team_ext_id}_light.svg"
    return ""


def form_dots_html(hits: list[bool | None], direction: str) -> str:
    """hits: list of True/False/None for last N games, most recent last."""
    dots = []
    for h in hits:
        if h is None:
            dots.append('<span class="dot empty">–</span>')
        elif (h and direction == "over") or (not h and direction == "under"):
            dots.append('<span class="dot hit">✓</span>')
        else:
            dots.append('<span class="dot miss">✗</span>')
    return '<div class="form-dots">' + "".join(dots) + "</div>"


def edge_bar_html(edge: float) -> str:
    pct = min(100, max(0, edge * 200))
    return (f'<div class="edge-bar-bg">'
            f'<div class="edge-bar-fill" style="width:{pct:.0f}%"></div></div>')


def _html(s: str) -> str:
    """Sanitize generated HTML for st.markdown.

    Streamlit runs Markdown over HTML even with unsafe_allow_html=True, so a
    blank line followed by an indented tag (which happens whenever an optional
    row like the line-movement block renders empty) gets parsed as a code
    block. Stripping leading whitespace and dropping blank lines makes the
    output immune to that.
    """
    return "\n".join(ln.strip() for ln in s.splitlines() if ln.strip())


def simulate_bankroll(picks_df: pd.DataFrame):
    """Flat 1-unit paper P&L over settled picks, chronological.

    Each leg risks 1 unit and pays the per-leg equivalent of a 2-pick 3x parlay
    (decimal √3 → +0.732u on a win, -1u on a loss, 0 on a push). Flat staking
    (not Kelly) keeps the curve honest — no compounding distortion. Returns
    (daily cumulative-P&L curve, metrics).
    """
    import math
    WIN_PL = math.sqrt(3.0) - 1.0       # ≈ +0.732u per winning leg
    d = (picks_df[picks_df["leg_result"].isin(["win", "loss", "push"])]
         .sort_values(["pick_date", "pick_id"]))
    cum = peak = 0.0
    max_dd = 0.0
    wins = losses = 0
    rows = []
    for _, r in d.iterrows():
        res = r["leg_result"]
        if res == "win":
            cum += WIN_PL; wins += 1
        elif res == "loss":
            cum -= 1.0; losses += 1
        peak = max(peak, cum); max_dd = max(max_dd, peak - cum)
        rows.append({"date": pd.to_datetime(r["pick_date"]), "pnl": cum})
    if not rows or (wins + losses) == 0:
        return pd.DataFrame(), {}
    curve = pd.DataFrame(rows).groupby("date", as_index=True)["pnl"].last()
    n = wins + losses
    m = {"units": cum, "n": n, "wins": wins, "losses": losses,
         "win_rate": wins / n, "yield": cum / n, "max_dd": max_dd}
    return curve, m


def _prediction_notice(sport: str, err: Exception) -> None:
    """Render a clean, non-alarming notice instead of a raw traceback.

    Live game-model inference can fail on the deploy box (e.g. a missing
    libgomp for LightGBM); the nightly cron still populates predictions, so
    point users there rather than leaking the exception text.
    """
    import os
    msg = (f"{sport} game predictions are refreshing — they populate after "
           "the daily model run. Check back shortly.")
    if os.getenv("LOG_LEVEL", "").upper() == "DEBUG":
        msg += f"\n\n`{type(err).__name__}: {err}`"
    st.info(msg, icon="⏳")


# ── Data loading ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def load_todays_picks():
    sql = """
        SELECT
            pk.pick_id, pk.player_id, pk.game_id,
            g.sport_code,
            p.full_name       AS player,
            p.external_id     AS player_ext_id,
            t.abbreviation    AS team,
            t.external_id     AS team_ext_id,
            pk.stat_type,
            pl.line_value     AS line,
            pk.direction,
            pk.model_prob     AS model_prob,
            pk.edge,
            COALESCE(pk.market_edge, pk.edge) AS market_edge,
            pk.expected_value AS kelly,
            pk.leg_result,
            pk.actual_value,
            pk.line_open,
            pk.line_movement,
            COALESCE(pk.injury_flag, 0) AS injury_flag,
            inj.status        AS injury_status,
            inj.short_comment AS injury_note,
            g.status          AS game_status,
            ht.abbreviation   AS home_team,
            at.abbreviation   AS away_team,
            wx.temp_f         AS wx_temp,
            wx.wind_out_mph   AS wx_wind_out,
            wx.is_dome        AS wx_dome,
            pr.predicted_mean AS predicted_mean,
            pr.distribution   AS distribution
        FROM picks pk
        JOIN players p    USING (player_id)
        LEFT JOIN teams t ON t.team_id = p.current_team_id
        JOIN games   g    USING (game_id)
        JOIN prop_lines pl ON pl.line_id = pk.line_id
        LEFT JOIN teams ht ON ht.team_id = g.home_team_id
        LEFT JOIN teams at ON at.team_id = g.away_team_id
        LEFT JOIN game_weather wx ON wx.game_id = pk.game_id
        LEFT JOIN predictions pr ON pr.prediction_id = pk.prediction_id
        -- The player's own current injury status (warn on Out / IL / Day-To-Day),
        -- most recent report within ~36h, matched by name within the sport.
        LEFT JOIN LATERAL (
            SELECT pi.status, pi.short_comment
            FROM player_injuries pi
            WHERE pi.sport_code = g.sport_code
              AND lower(pi.player_name) = lower(p.full_name)
              AND pi.fetched_at > NOW() - INTERVAL '36 hours'
            ORDER BY pi.fetched_at DESC
            LIMIT 1
        ) inj ON true
        WHERE (pk.picked_at AT TIME ZONE 'America/Los_Angeles')::date
              = (NOW() AT TIME ZONE 'America/Los_Angeles')::date
          -- Only games actually happening today: excludes stale picks logged
          -- against already-played games (e.g. unresolved PPL placeholders from
          -- a prior day) and early picks for tomorrow.
          AND g.game_date = (NOW() AT TIME ZONE 'America/Los_Angeles')::date
          AND (pk.leg_result IS NULL OR pk.leg_result != 'void')
        ORDER BY COALESCE(pk.market_edge, pk.edge) DESC
    """
    return pd.read_sql(text(sql), engine)


COMBO_STAT_SQL = {
    "pts_rebs_asts": "COALESCE((pg.stats->>'points')::float,0) + COALESCE((pg.stats->>'rebounds')::float,0) + COALESCE((pg.stats->>'assists')::float,0)",
    "pts_rebs":      "COALESCE((pg.stats->>'points')::float,0) + COALESCE((pg.stats->>'rebounds')::float,0)",
    "pts_asts":      "COALESCE((pg.stats->>'points')::float,0) + COALESCE((pg.stats->>'assists')::float,0)",
    "rebs_asts":     "COALESCE((pg.stats->>'rebounds')::float,0) + COALESCE((pg.stats->>'assists')::float,0)",
    "blocks_steals": "COALESCE((pg.stats->>'blocks')::float,0) + COALESCE((pg.stats->>'steals')::float,0)",
    # NBA box scores use fg3_made for threes
    "threes_made":   "COALESCE((pg.stats->>'fg3_made')::float,(pg.stats->>'threes_made')::float,0)",
    # Home runs uses same key
    "home_runs":     "COALESCE((pg.stats->>'home_runs')::float,0)",
}

@st.cache_data(ttl=300)
def load_player_form(player_ids: tuple, stat_type: str, sport: str):
    """Return last 10 actuals per player for a given stat_type."""
    if not player_ids:
        return pd.DataFrame()
    actual_expr = COMBO_STAT_SQL.get(
        stat_type,
        f"COALESCE((pg.stats->>{repr(stat_type)})::float, 0)"
    )
    sql = f"""
        SELECT player_id, game_date, actual FROM (
            SELECT pg.player_id, g.game_date,
                   {actual_expr} AS actual,
                   ROW_NUMBER() OVER (PARTITION BY pg.player_id ORDER BY g.game_date DESC) AS rn
            FROM player_games pg
            JOIN games g USING (game_id)
            WHERE pg.player_id = ANY(:pids)
              AND g.sport_code  = :sport
              AND g.status = 'final'
              AND g.game_date < CURRENT_DATE
        ) sub WHERE rn <= 10
        ORDER BY player_id, game_date DESC
    """
    df = pd.read_sql(text(sql), engine,
                     params={"pids": list(player_ids), "sport": sport})
    return df


@st.cache_data(ttl=300)
def load_historical_summary():
    sql = """
        SELECT g.sport_code, pk.stat_type, pk.direction,
               COUNT(*)                                                    AS picks,
               COUNT(*) FILTER (WHERE pk.leg_result = 'win')              AS wins,
               COUNT(*) FILTER (WHERE pk.leg_result = 'loss')             AS losses,
               COUNT(*) FILTER (WHERE pk.leg_result = 'push')             AS pushes,
               ROUND(100.0 * COUNT(*) FILTER (WHERE pk.leg_result = 'win')
                   / NULLIF(COUNT(*) FILTER (
                       WHERE pk.leg_result IN ('win','loss')), 0), 1)     AS win_pct,
               ROUND(AVG(pk.model_prob)::numeric, 3)                      AS avg_prob,
               ROUND(AVG(pk.edge)::numeric, 3)                            AS avg_edge
        FROM picks pk JOIN games g USING (game_id)
        WHERE pk.leg_result IN ('win','loss','push')
        GROUP BY g.sport_code, pk.stat_type, pk.direction
        ORDER BY picks DESC
    """
    return pd.read_sql(text(sql), engine)


@st.cache_data(ttl=300)
def load_backtest_trend():
    """Historical backtest run results for trend chart."""
    sql = """
        SELECT run_at::date AS date, sport, n_picks,
               ROUND(win_rate * 100, 1)      AS win_pct,
               ROUND(roi_2pick * 100, 1)     AS roi_2pick_pct,
               ROUND(edge_10_win_rate * 100, 1) AS edge10_win_pct,
               trigger
        FROM backtest_runs
        ORDER BY run_at DESC
        LIMIT 50
    """
    return pd.read_sql(text(sql), engine)


@st.cache_data(ttl=300)
def load_daily_backtest():
    """Daily walk-forward backtest snapshots (props.picks.daily_backtest)."""
    try:
        return pd.read_sql(text("""
            SELECT run_date, window_days, rec_n, rec_w, rec_l,
                   ROUND(rec_winrate * 100, 1)   AS rec_win_pct,
                   ROUND(rec_roi_2pick * 100, 1) AS rec_roi_pct,
                   ROUND(brier::numeric, 3)      AS brier, detail
            FROM backtest_daily
            ORDER BY run_date DESC
            LIMIT 60
        """), engine)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=300)
def load_soft_lines():
    """Today's soft lines (PrizePicks vs sharp market) — see props.picks.soft_lines."""
    try:
        return pd.read_sql(text("""
            SELECT sport_code, player_name, stat_type, best_side, pp_line,
                   sharp_line, best_prob, edge
            FROM soft_lines
            WHERE run_date = (NOW() AT TIME ZONE 'America/Los_Angeles')::date
            ORDER BY edge DESC
        """), engine)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=300)
def load_sharp_clv():
    """Settled picks with a sharp pick-time AND close prob — for sharp-market CLV."""
    try:
        return pd.read_sql(text("""
            SELECT (market_prob_close - market_prob)::float AS clv, leg_result
            FROM picks
            WHERE market_prob IS NOT NULL AND market_prob_close IS NOT NULL
              AND leg_result IN ('win', 'loss')
        """), engine)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=300)
def load_all_settled_picks():
    """Full settled pick history for analytics."""
    sql = """
        SELECT
            pk.pick_id,
            pk.picked_at::date          AS pick_date,
            g.sport_code,
            pk.stat_type,
            pk.direction,
            pk.model_prob,
            COALESCE(pk.market_edge, pk.edge) AS market_edge,
            pk.edge,
            pk.leg_result,
            pk.actual_value,
            pl.line_value               AS line,
            pk.line_close
        FROM picks pk
        JOIN games g     USING (game_id)
        LEFT JOIN prop_lines pl ON pl.line_id = pk.line_id
        WHERE pk.leg_result IN ('win','loss','push')
        ORDER BY pk.picked_at
    """
    return pd.read_sql(text(sql), engine)


@st.cache_data(ttl=120)
def load_recent_picks(days: int = 7):
    sql = """
        SELECT pk.picked_at::date AS date, g.sport_code,
               p.full_name AS player, pk.stat_type, pl.line_value AS line,
               pk.direction, pk.model_prob, pk.edge, pk.leg_result, pk.actual_value
        FROM picks pk
        JOIN players p USING (player_id)
        JOIN games   g USING (game_id)
        JOIN prop_lines pl ON pl.line_id = pk.line_id
        WHERE (pk.picked_at AT TIME ZONE 'America/Los_Angeles')::date
              >= (NOW() AT TIME ZONE 'America/Los_Angeles')::date - :days
          AND (pk.leg_result IS NULL OR pk.leg_result != 'void')
        ORDER BY pk.picked_at DESC
    """
    return pd.read_sql(text(sql), engine, params={"days": days})


@st.cache_data(ttl=300)
@st.cache_data(ttl=300)
def load_player_options():
    """Distinct players that have a settled pick — for the detail-page selector."""
    return pd.read_sql(text("""
        SELECT DISTINCT p.full_name AS name
        FROM picks pk JOIN players p USING (player_id)
        WHERE pk.leg_result IN ('win','loss','push')
        ORDER BY 1
    """), engine)["name"].tolist()


@st.cache_data(ttl=600)
def load_team_index():
    """Every league's FULL canonical roster (MLB 30, NBA 30, NHL 32, WNBA 15),
    straight from the teams table — so the lookup shows all teams, not just the
    handful we happen to have picks for. Excludes the PrizePicks placeholder and
    the MLB All-Star 'teams'. Kept fresh by props.ingest.{mlb,nhl}_teams."""
    return pd.read_sql(text("""
        SELECT sport_code AS sport, abbreviation AS team, name
        FROM teams
        WHERE COALESCE(external_id,'') <> 'PP_PLACEHOLDER'
          AND COALESCE(name,'') NOT ILIKE '%All-Star%'
          AND COALESCE(abbreviation,'') <> ''
        ORDER BY sport_code, abbreviation
    """), engine)


@st.cache_data(ttl=300)
def load_player_index():
    """(sport, team, player) for every CURRENTLY-ACTIVE player, mapped to their
    most-recent game's team — drives the Team → Player step of the lookup.

    "Active" = most-recent game within 150 days of that sport's latest game, which
    keeps the list to current rosters and drops players who've moved on but whose
    last game we have is stale (e.g. Cam Reddish / Armel Traoré last logged a
    Lakers game in Oct 2024 — they'd otherwise still show as Lakers). Recent-game
    team also avoids the stale players.current_team_id (Fox on SAC post-trade).
    Placeholder team excluded."""
    return pd.read_sql(text("""
        WITH sportmax AS (
            SELECT sport_code, MAX(game_date) AS smax
            FROM games WHERE status = 'final' GROUP BY 1
        )
        SELECT sport, team, player FROM (
            SELECT g.sport_code AS sport, t.abbreviation AS team, p.full_name AS player,
                   g.game_date AS gd,
                   ROW_NUMBER() OVER (PARTITION BY pg.player_id
                                      ORDER BY g.game_date DESC, pg.player_game_id DESC) AS rn
            FROM player_games pg
            JOIN players p USING (player_id)
            JOIN games g ON g.game_id = pg.game_id
            JOIN teams t ON t.team_id = pg.team_id
            WHERE COALESCE(t.external_id,'') <> 'PP_PLACEHOLDER'
        ) x
        JOIN sportmax s ON s.sport_code = x.sport
        WHERE x.rn = 1 AND x.gd >= s.smax - 150
        ORDER BY sport, team, player
    """), engine)


@st.cache_data(ttl=300)
def load_picked_players():
    """Names with at least one settled pick — the meaningful set to follow on a
    watchlist (vs every rostered player)."""
    return pd.read_sql(text("""
        SELECT DISTINCT p.full_name AS player
        FROM picks pk JOIN players p USING (player_id)
        WHERE pk.leg_result IN ('win','loss','push')
        ORDER BY 1
    """), engine)["player"].tolist()


@st.cache_data(ttl=300)
def load_player_detail(name: str):
    """All settled picks for one player — for the player detail page."""
    return pd.read_sql(text("""
        SELECT pk.picked_at::date AS date, g.sport_code, pk.stat_type,
               pl.line_value AS line, pk.direction, pk.model_prob::float AS model_prob,
               pk.leg_result, pk.actual_value::float AS actual
        FROM picks pk
        JOIN players p USING (player_id)
        JOIN games g USING (game_id)
        LEFT JOIN prop_lines pl ON pl.line_id = pk.line_id
        WHERE lower(p.full_name) = lower(:nm) AND pk.leg_result IN ('win','loss','push')
        ORDER BY pk.picked_at DESC
    """), engine, params={"nm": name})


@st.cache_data(ttl=600)
def load_player_why(name: str):
    """'Why this pick' for an MLB batter: the top model drivers (SHAP-lite) for
    total_bases + hits, from their latest game's feature vector. None for
    non-MLB / no data. Recomputed on demand — no schema or pick-path change."""
    try:
        from props.features.inference import build_full_feature_vector
        from props.models.explain import explain
    except Exception:
        return None
    row = pd.read_sql(text("""
        SELECT pg.player_id, g.game_date, g.season,
          (SELECT pi.player_id FROM player_games pi WHERE pi.game_id = pg.game_id
             AND pi.team_id = pg.opponent_id AND (pi.stats->>'batters_faced')::int > 0
             ORDER BY (pi.stats->>'batters_faced')::int DESC LIMIT 1) AS opp
        FROM player_games pg JOIN players p USING (player_id) JOIN games g USING (game_id)
        WHERE lower(p.full_name) = lower(:n) AND g.sport_code = 'mlb'
          AND (pg.stats->>'plate_appearances')::int > 0
        ORDER BY g.game_date DESC LIMIT 1
    """), engine, params={"n": name})
    if row.empty:
        return None
    r = row.iloc[0]
    try:
        feats = build_full_feature_vector(int(r["player_id"]), r["game_date"],
                                          int(r["season"]),
                                          int(r["opp"]) if pd.notna(r["opp"]) else None)
        out = {stat: explain(model, feats)
               for stat, model in (("total bases", "total_bases_v1"), ("hits", "hits_v1"))}
        return {k: v for k, v in out.items() if v} or None
    except Exception:
        return None


def _home_button(key: str):
    """Back to the main dashboard — clears the drill-down query params."""
    if st.button("🏠 Home", key=key):
        for k in ("player", "view"):
            st.query_params.pop(k, None)
        st.rerun()


def render_player_view(name: str):
    """Read-only drill-down on one player (reached via ?player=…)."""
    _home_button("home_player")
    st.markdown(f"### 🔎 {name} — pick history")
    df = load_player_detail(name)
    if df.empty:
        st.info(f"No settled picks for {name}.")
        st.stop()
    wl = df[df["leg_result"].isin(["win", "loss"])]
    w = int((wl["leg_result"] == "win").sum())
    c1, c2, c3 = st.columns(3)
    c1.metric("Record", f"{w}–{len(wl)-w}", f"{w/len(wl)*100:.0f}% win" if len(wl) else "—")
    c2.metric("Picks tracked", len(df))
    c3.metric("Sports", ", ".join(sorted(df["sport_code"].str.upper().unique())))
    # Why the model rates them — per-prediction feature contributions (SHAP-lite)
    _why = load_player_why(name)
    if _why:
        st.markdown("##### 🧠 Why — what drives the model's latest projection")
        for stat, drivers in _why.items():
            st.caption(f"**{stat}** — {drivers}")
    # by stat × direction
    g = (wl.groupby(["stat_type", "direction"])
           .agg(n=("leg_result", "size"),
                w=("leg_result", lambda s: (s == "win").sum())).reset_index())
    g["Win %"] = (g["w"] / g["n"] * 100).map(lambda x: f"{x:.0f}%")
    g["Record"] = g.apply(lambda r: f"{int(r['w'])}–{int(r['n']-r['w'])}", axis=1)
    st.markdown("##### By stat × direction")
    st.dataframe(g[["stat_type", "direction", "Record", "Win %"]]
                 .rename(columns={"stat_type": "Stat", "direction": "Dir"}),
                 use_container_width=True, hide_index=True)
    st.markdown("##### Recent picks")
    st.dataframe(df.head(40), use_container_width=True, hide_index=True)
    st.stop()


def render_ops_view():
    """Read-only ops snapshot — cost/usage + a live dashboard health check
    (reached via ?view=ops). Odds credits, scrape volume, pipeline freshness, and
    DB growth in one place so a blow-up is visible before it bites."""
    _home_button("home_ops")
    st.markdown("### 🛠️ Ops — cost / usage")
    try:
        from props.ops.usage import gather
        m = gather()
    except Exception as e:
        st.error(f"Couldn't load usage snapshot: {e}")
        st.stop()

    s, p = m["scrape"], m["picks"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Lines today", s["today_lines"], f"10d avg {s['avg_10d']:.0f}")
    c2.metric("Picks today", p["today_n"], f"{p['settled_7d']} settled / 7d")
    c3.metric("DB size", m["db"]["size"])
    st.caption(f"🎰 Odds API — {m['odds']['detail']}")
    _stale = (s["last_scrape_hours"] or 0) > 18 or (p["last_picked_hours"] or 0) > 36
    _emoji = "🔴" if _stale else "🟢"
    st.caption(f"{_emoji} last scrape {s['last_scrape_hours']}h ago · "
               f"last slate {p['last_picked_hours']}h ago"
               + ("  — pipeline looks stale" if _stale else ""))

    if s["by_day"]:
        sv = pd.DataFrame(s["by_day"][::-1], columns=["date", "lines"]).set_index("date")
        st.markdown("##### Scrape volume — distinct lines / day")
        st.bar_chart(sv, height=170)

    st.markdown("##### Database — biggest tables")
    st.dataframe(pd.DataFrame(m["db"]["tables"], columns=["Table", "Size"]),
                 use_container_width=True, hide_index=True)
    st.caption(f"💳 Railway $ isn't exposed by API — see the "
               f"[Railway usage page]({m['railway_billing_url']}); DB size is the proxy.")

    st.markdown("##### Data accuracy")
    try:
        from props.ops.data_audit import run_checks as _audit
        for f in _audit():
            st.write(("⚠️ " if f["level"] == "warn" else "✅ ")
                     + f"**{f['name']}** — {f['detail']}")
    except Exception as e:
        st.caption(f"data audit unavailable: {e}")

    st.markdown("##### Feature drift")
    try:
        from props.ops.feature_drift import run_checks as _drift
        for f in _drift():
            st.write(("⚠️ " if f["level"] == "warn" else "✅ ")
                     + f"**{f['name']}** — {f['detail']}")
    except Exception as e:
        st.caption(f"feature drift unavailable: {e}")

    st.markdown("##### Dashboard health")
    if st.button("Run live health check", key="ops_health"):
        try:
            from props.ops.dashboard_monitor import run_checks
            for f in run_checks():
                st.write(("⚠️ " if f["level"] == "warn" else "✅ ")
                         + f"**{f['name']}** — {f['detail']}")
        except Exception as e:
            st.caption(f"health check unavailable: {e}")
    else:
        st.caption("Times /_stcore/health + a real render (the app pings itself).")
    st.stop()


@st.cache_data(ttl=300)
def load_results_summary():
    """Headline record for the public results view — overall + recommended-tier
    W/L and per-sport, computed from all settled picks."""
    df = pd.read_sql(text("""
        SELECT sport_code, stat_type, direction, model_prob::float AS model_prob, leg_result
        FROM picks WHERE leg_result IN ('win','loss') AND model_prob IS NOT NULL
    """), engine)
    if df.empty:
        return None
    df["_rec"] = df.apply(
        lambda r: r["model_prob"] >= rec_cutoff(r["sport_code"], r["stat_type"],
                                                direction=r["direction"]), axis=1)

    def wl(d):
        w = int((d["leg_result"] == "win").sum()); return w, len(d)

    rec_df = df[df["_rec"]]
    by_sport = {sp: wl(g[g["_rec"]]) for sp, g in df.groupby("sport_code")
                if g["_rec"].any()}
    return {"overall": wl(df), "rec": wl(rec_df), "by_sport": by_sport}


@st.cache_data(ttl=300)
def load_pick_history():
    """Every settled pick — for the filterable history browser."""
    return pd.read_sql(text("""
        SELECT (pk.picked_at AT TIME ZONE 'America/Los_Angeles')::date AS date,
               g.sport_code AS sport, p.full_name AS player, pk.stat_type AS stat,
               pk.direction AS dir, pl.line_value AS line,
               pk.model_prob::float AS model_prob, pk.actual_value::float AS actual,
               pk.leg_result AS result
        FROM picks pk
        JOIN players p USING (player_id)
        JOIN games g USING (game_id)
        LEFT JOIN prop_lines pl ON pl.line_id = pk.line_id
        WHERE pk.leg_result IN ('win','loss','push')
        ORDER BY pk.picked_at DESC
    """), engine)


def render_history_view():
    """Filterable settled-pick browser with CSV export (reached via ?view=history)."""
    _home_button("home_history")
    st.markdown("### 📜 Pick history")
    df = load_pick_history()
    if df.empty:
        st.info("No settled picks yet.")
        st.stop()
    c1, c2, c3 = st.columns(3)
    sports = c1.multiselect("Sport", sorted(df["sport"].unique()), key="h_sport")
    stats = c2.multiselect("Stat", sorted(df["stat"].unique()), key="h_stat")
    results = c3.multiselect("Result", ["win", "loss", "push"], key="h_result")
    dmin, dmax = df["date"].min(), df["date"].max()
    dr = st.date_input("Date range", value=(dmin, dmax),
                       min_value=dmin, max_value=dmax, key="h_date")
    f = df
    if sports:
        f = f[f["sport"].isin(sports)]
    if stats:
        f = f[f["stat"].isin(stats)]
    if results:
        f = f[f["result"].isin(results)]
    if isinstance(dr, tuple) and len(dr) == 2:
        f = f[(f["date"] >= dr[0]) & (f["date"] <= dr[1])]
    wl = f[f["result"].isin(["win", "loss"])]
    w = int((wl["result"] == "win").sum())
    st.caption(f"{len(f)} picks · record {w}–{len(wl) - w}"
               + (f" ({w / len(wl) * 100:.0f}%)" if len(wl) else ""))
    st.dataframe(f, use_container_width=True, hide_index=True)
    st.download_button("⬇️ Download CSV", f.to_csv(index=False).encode(),
                       "prop-edge-picks.csv", "text/csv", key="h_csv")
    st.stop()


@st.cache_data(ttl=300)
def load_bankroll():
    """Cumulative paper P&L for the recommended tier — each rec leg as a flat 1u
    bet at 2-pick-equivalent odds (a 2-pick 3x parlay breaks even at p=0.577 per
    leg → decimal 1.732, so win=+0.732u, loss=−1u, push=0)."""
    df = pd.read_sql(text("""
        SELECT (pk.picked_at AT TIME ZONE 'America/Los_Angeles')::date AS date,
               g.sport_code AS sport, pk.stat_type AS stat, pk.direction AS dir,
               pk.model_prob::float AS model_prob, pk.leg_result AS result
        FROM picks pk JOIN games g USING (game_id)
        WHERE pk.leg_result IN ('win','loss','push') AND pk.model_prob IS NOT NULL
        ORDER BY pk.picked_at
    """), engine)
    if df.empty:
        return None
    rec = df[df.apply(lambda r: r["model_prob"] >= rec_cutoff(
        r["sport"], r["stat"], direction=r["dir"]), axis=1)].copy()
    if rec.empty:
        return None
    rec["pnl"] = rec["result"].map({"win": 0.732, "loss": -1.0, "push": 0.0})
    rec["cum"] = rec["pnl"].cumsum()
    curve = rec.groupby("date")["cum"].last()
    n = int((rec["result"] != "push").sum())
    total = float(rec["pnl"].sum())
    return {"curve": curve, "total_units": total, "n": n,
            "roi": (total / n * 100 if n else 0.0)}


def render_results_view():
    """Clean, shareable read-only record (reached via ?view=results)."""
    _home_button("home_results")
    s = load_results_summary()
    st.markdown(
        '<h1 style="font-size:2.2rem;font-weight:900;letter-spacing:-0.03em;margin-bottom:0">'
        '<span style="background:linear-gradient(135deg,#9d7bff,#22d3ee);'
        '-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text">'
        '⚡ prop-edge — track record</span></h1>'
        '<p style="color:#5f6678;margin-top:2px;font-size:0.82rem;text-transform:uppercase;'
        'letter-spacing:0.08em">paper-tracking · not betting advice</p>',
        unsafe_allow_html=True)
    if not s:
        st.info("No settled picks yet.")
        st.stop()
    rw, rn = s["rec"]; aw, an = s["overall"]
    c1, c2, c3 = st.columns(3)
    rwr = rw / rn * 100 if rn else 0
    c1.metric("Recommended tier", f"{rw}–{rn-rw}", f"{rwr:.1f}% win",
              delta_color="normal" if rwr >= 57.7 else "inverse")
    c2.metric("vs 57.7% breakeven", f"{rwr-57.7:+.1f} pts",
              "2-pick parlay breakeven", delta_color="normal" if rwr >= 57.7 else "inverse")
    c3.metric("All picks", f"{aw}–{an-aw}", f"{aw/an*100:.1f}% win" if an else "—")
    st.caption(f"{an} picks settled. The recommended tier is the slate the system "
               "actually surfaces — picks clearing an auto-tuned per-category cutoff.")
    rows = []
    for sp, (w, n) in sorted(s["by_sport"].items(), key=lambda x: -x[1][1]):
        rows.append({"Sport": sp.upper(), "Recommended record": f"{w}–{n-w}",
                     "Win rate": f"{w/n*100:.1f}%" if n else "—",
                     "vs 57.7%": f"{w/n*100-57.7:+.1f}%" if n else "—"})
    if rows:
        st.markdown("##### Recommended tier by sport")
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    bk = load_bankroll()
    if bk:
        st.markdown("##### 💰 Bankroll (paper) — recommended tier")
        b1, b2, b3 = st.columns(3)
        b1.metric("Net units", f"{bk['total_units']:+.1f}u",
                  delta_color="normal" if bk["total_units"] >= 0 else "inverse")
        b2.metric("ROI", f"{bk['roi']:+.1f}%",
                  delta_color="normal" if bk["roi"] >= 0 else "inverse")
        b3.metric("Bets", bk["n"])
        st.line_chart(bk["curve"], height=200)
        st.caption("Each recommended leg as a flat 1u bet at 2-pick-equivalent odds "
                   "(1.73 / −137; 57.7% breakeven). Paper-tracking, not betting advice.")
    st.stop()


def _player_panel(name: str):
    """One side of the comparison: record + by-stat×direction table."""
    d = load_player_detail(name)
    if d.empty:
        st.info("No settled picks.")
        return
    wl = d[d["leg_result"].isin(["win", "loss"])]
    w = int((wl["leg_result"] == "win").sum())
    st.metric("Record", f"{w}–{len(wl) - w}",
              f"{w / len(wl) * 100:.0f}% win" if len(wl) else "—")
    g = (wl.groupby(["stat_type", "direction"])
           .agg(n=("leg_result", "size"),
                w=("leg_result", lambda s: (s == "win").sum())).reset_index())
    g["Win %"] = (g["w"] / g["n"] * 100).map(lambda x: f"{x:.0f}%")
    g["Rec"] = g.apply(lambda r: f"{int(r['w'])}–{int(r['n'] - r['w'])}", axis=1)
    st.dataframe(g[["stat_type", "direction", "Rec", "Win %"]]
                 .rename(columns={"stat_type": "Stat", "direction": "Dir"}),
                 use_container_width=True, hide_index=True)


def render_compare_view():
    """Two players side by side (reached via ?view=compare)."""
    _home_button("home_compare")
    st.markdown("### ⚖️ Compare players")
    players = load_picked_players()
    c1, c2 = st.columns(2)
    a = c1.selectbox("Player A", ["—"] + players, key="cmp_a")
    b = c2.selectbox("Player B", ["—"] + players, key="cmp_b")
    if a == "—" or b == "—":
        st.caption("Pick two players to compare their settled records.")
        st.stop()
    pa, pb = st.columns(2)
    with pa:
        st.markdown(f"#### {a}")
        _player_panel(a)
    with pb:
        st.markdown(f"#### {b}")
        _player_panel(b)
    st.stop()


@st.cache_data(ttl=60)
def load_game_predictions_data():
    """Fetch today's game predictions if winner model has run."""
    sql = """
        SELECT g.game_id,
               ht.city || ' ' || ht.name AS home_team,
               at.city || ' ' || at.name AS away_team,
               ht.external_id AS home_ext, at.external_id AS away_ext,
               ht.abbreviation AS home_abbr, at.abbreviation AS away_abbr,
               g.game_date,
               (g.context->>'home_win_prob')::float  AS home_win_prob,
               (g.context->>'implied_margin')::float AS implied_margin,
               (g.context->>'market_spread')::float  AS market_spread,
               (g.context->>'market_total')::float   AS market_total,
               (g.context->>'market_edge')::float    AS market_edge
        FROM games g
        JOIN teams ht ON ht.team_id = g.home_team_id
        JOIN teams at ON at.team_id = g.away_team_id
        WHERE g.game_date = (NOW() AT TIME ZONE 'America/Los_Angeles')::date
          AND g.sport_code = 'nba'
          AND g.context ? 'home_win_prob'
        ORDER BY g.game_id
    """
    return pd.read_sql(text(sql), engine)


@st.cache_data(ttl=60)
def load_mlb_game_predictions():
    """Today's MLB game-winner predictions straight from games.context (the cron
    persists win prob, margin, and probable pitchers each morning). The dashboard
    reads only — no live schedule fetch or LightGBM inference on render."""
    sql = """
        SELECT g.game_id,
               COALESCE(ht.city || ' ', '') || ht.name AS home_team,
               COALESCE(at.city || ' ', '') || at.name AS away_team,
               (g.context->>'home_win_prob')::float  AS home_win_prob,
               (g.context->>'implied_margin')::float AS implied_margin,
               g.context->>'home_pitcher'            AS home_pitcher,
               g.context->>'away_pitcher'            AS away_pitcher
        FROM games g
        JOIN teams ht ON ht.team_id = g.home_team_id
        JOIN teams at ON at.team_id = g.away_team_id
        WHERE g.sport_code = 'mlb'
          AND g.game_date = (NOW() AT TIME ZONE 'America/Los_Angeles')::date
          AND g.context ? 'home_win_prob'
        ORDER BY g.game_id
    """
    return pd.read_sql(text(sql), engine)


def _american_to_prob(odds) -> float | None:
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None
    return (-o) / (-o + 100) if o < 0 else 100 / (o + 100)


@st.cache_data(ttl=300)
def load_market_games(sport_path: str, date_str: str) -> list[dict]:
    """Market-implied game cards from ESPN's public scoreboard.

    NHL/WNBA have no trained winner model and too little history to train one,
    so we surface the sportsbook's de-vigged moneyline win probability instead.
    Clearly market-derived — not a model prediction.
    """
    from curl_cffi import requests as cc
    url = f"https://site.api.espn.com/apis/site/v2/sports/{sport_path}/scoreboard"
    try:
        data = cc.get(url, params={"dates": date_str, "limit": 30},
                      impersonate="chrome120", timeout=15).json()
    except Exception:
        return []

    def _ml(side: dict):
        for k in ("close", "current", "open"):
            v = (side.get(k) or {}).get("odds")
            if v not in (None, "", "OFF", "EVEN"):
                return v
        return None

    out = []
    for ev in data.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        cs = comp.get("competitors", [])
        h = next((c for c in cs if c.get("homeAway") == "home"), None)
        a = next((c for c in cs if c.get("homeAway") == "away"), None)
        if not h or not a:
            continue
        state = comp.get("status", {}).get("type", {}).get("state")
        odds = comp.get("odds") or []
        home_wp = spread = total = None
        if odds:
            o0 = odds[0]
            spread = o0.get("spread")
            total = o0.get("overUnder")
            ml = o0.get("moneyline") or {}
            hml, aml = _ml(ml.get("home") or {}), _ml(ml.get("away") or {})
            ph, pa = _american_to_prob(hml), _american_to_prob(aml)
            if ph is not None and pa is not None and (ph + pa) > 0:
                home_wp = ph / (ph + pa)   # de-vig
        out.append({
            "home": h.get("team", {}).get("displayName", "Home"),
            "away": a.get("team", {}).get("displayName", "Away"),
            "home_abbr": h.get("team", {}).get("abbreviation", ""),
            "away_abbr": a.get("team", {}).get("abbreviation", ""),
            "home_wp": home_wp, "spread": spread, "total": total,
            "state": state,
        })
    return out


# ── Live in-game tracker ──────────────────────────────────────────────────────

ESPN_LIVE_PATH = {"nba": "basketball/nba", "wnba": "basketball/wnba",
                  "nhl": "hockey/nhl", "mlb": "baseball/mlb"}


def _norm_name(name: str) -> str:
    import unicodedata, re
    n = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z]", "", n)


def _statnum(x):
    """Parse an ESPN stat cell: '14' -> 14, '2-5' (3PT made-att) -> 2 (made)."""
    try:
        s = str(x).strip()
        return float(s.split("-")[0]) if "-" in s else float(s)
    except (TypeError, ValueError):
        return None


@st.cache_data(ttl=30)
def load_live_stats(sport: str) -> dict:
    """Live in-game player stats from ESPN for games currently in progress.

    Returns {normalized_player_name: {"raw": {ESPN_STAT: value}, "status": "Q4 6:23"}}.
    Only includes games in the 'in' (live) state. 30s cache.
    """
    from curl_cffi import requests as cc
    path = ESPN_LIVE_PATH.get(sport)
    if not path:
        return {}
    try:
        sb = cc.get(f"https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard",
                    impersonate="chrome120", timeout=12).json()
    except Exception:
        return {}
    out: dict = {}
    for ev in sb.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        stt = comp.get("status", {}).get("type", {})
        if stt.get("state") != "in":
            continue
        status_text = stt.get("shortDetail") or stt.get("detail") or "LIVE"
        try:
            summ = cc.get(f"https://site.api.espn.com/apis/site/v2/sports/{path}/summary",
                          params={"event": ev["id"]}, impersonate="chrome120", timeout=12).json()
        except Exception:
            continue
        for team in summ.get("boxscore", {}).get("players", []):
            for grp in team.get("statistics", []):
                names = [n.upper() for n in grp.get("names", [])]
                for a in grp.get("athletes", []):
                    nm = _norm_name(a.get("athlete", {}).get("displayName", ""))
                    if not nm:
                        continue
                    raw = dict(zip(names, a.get("stats", [])))
                    # merge (a player may appear in batting+pitching groups)
                    out.setdefault(nm, {"raw": {}, "status": status_text})
                    out[nm]["raw"].update(raw)
    return out


def live_value(sport: str, stat_type: str, raw: dict):
    """Resolve our stat_type to a live value from ESPN's raw stat cells."""
    g = lambda k: _statnum(raw.get(k))
    def _sum(*xs):
        vs = [x for x in xs if x is not None]
        return sum(vs) if vs else None
    if sport in ("nba", "wnba"):
        P, R, A = g("PTS"), g("REB"), g("AST")
        m = {"points": P, "rebounds": R, "assists": A,
             "steals": g("STL"), "blocks": g("BLK"), "turnovers": g("TO"),
             "off_rebounds": g("OREB"), "def_rebounds": g("DREB"),
             "threes_made": g("3PT"),
             "pts_rebs_asts": _sum(P, R, A), "pts_rebs": _sum(P, R),
             "pts_asts": _sum(P, A), "rebs_asts": _sum(R, A),
             "blocks_steals": _sum(g("BLK"), g("STL"))}
        return m.get(stat_type)
    if sport == "nhl":
        return {"goals": g("G"), "assists": g("A"), "saves": g("SV"),
                "shots": g("SOG")}.get(stat_type)
    if sport == "mlb":
        # ESPN splits batting/pitching groups; be role-aware so a pitcher's
        # "hits allowed" (K/H in the pitching group) never shows as a batter stat.
        is_batter = "AB" in raw
        is_pitcher = "IP" in raw
        if stat_type == "strikeouts_pitcher":
            return g("K") if is_pitcher else None
        if stat_type in ("hits", "home_runs", "rbis", "runs"):
            if not is_batter:
                return None
            return {"hits": g("H"), "home_runs": g("HR"),
                    "rbis": g("RBI"), "runs": g("R")}.get(stat_type)
        # total_bases isn't in ESPN's scoreboard boxscore — no live value
        return None
    return None


def live_row_html(sport: str, stat_type: str, line: float, direction: str, live: dict) -> str:
    """A '🔴 LIVE' progress row for a pick whose game is in progress."""
    cur = live_value(sport, stat_type, live.get("raw", {}))
    if cur is None:
        return ""
    status = live.get("status", "LIVE")
    pct = min(100, max(0, (cur / line * 100) if line else 0))
    if direction == "over":
        cleared = cur >= line
        color = "#2ee6a6" if cleared else "#ffcf5c"
        txt = (f"✅ HIT {cur:g}/{line:g}" if cleared
               else f"{cur:g}/{line:g} · needs {line-cur:g}+")
    else:  # under
        safe = cur < line
        color = "#2ee6a6" if safe else "#ff5d6c"
        txt = (f"{cur:g}/{line:g} · {line-cur:g} cushion" if safe
               else f"❌ BUSTED {cur:g}/{line:g}")
    return f"""
<div class="prob-row" style="margin-top:8px">
  <span class="prob-label" style="color:#ff5d6c">🔴 LIVE · {status}</span>
  <span class="prob-value" style="color:{color}">{txt}</span>
</div>
<div class="edge-bar-bg" style="margin-top:4px">
  <div class="edge-bar-fill" style="width:{pct:.0f}%;background:{color};box-shadow:0 0 10px {color}"></div>
</div>"""


# ── Pick card builder ─────────────────────────────────────────────────────────

def build_pick_card(row, form_df: pd.DataFrame, live: dict = None) -> str:
    sport   = row["sport_code"]
    photo   = player_photo_url(row.get("player_ext_id", ""), sport)
    logo    = team_logo_url(row.get("team_ext_id", ""), sport)
    direction = row["direction"]
    line    = float(row["line"])
    # Displayed confidence is recalibrated (the raw model prob is over-confident);
    # selection/star still use the raw prob upstream, so this only changes the
    # number shown to the user, making it honest.
    prob    = calibrate(float(row["model_prob"]))
    edge    = float(row.get("market_edge") or row.get("edge") or 0)
    kelly   = float(row.get("kelly") or 0)
    inj     = float(row.get("injury_flag") or 0)

    # Stat type display name
    stat_labels = {
        "points": "Points", "rebounds": "Rebounds", "assists": "Assists",
        "threes_made": "3-PT Made", "pts_rebs_asts": "PRA",
        "pts_rebs": "P+R", "pts_asts": "P+A", "rebs_asts": "R+A",
        "blocks": "Blocks", "steals": "Steals", "turnovers": "Turnovers",
        "blocks_steals": "Blk+Stl", "def_rebounds": "D-Reb", "off_rebounds": "O-Reb",
        "strikeouts_pitcher": "Strikeouts", "hits": "Hits",
        "total_bases": "Total Bases", "rbis": "RBIs", "home_runs": "Home Runs",
        "goals": "Goals", "saves": "Saves",
        "fantasy_score": "Fantasy",
    }
    stat_label = stat_labels.get(row["stat_type"], row["stat_type"].replace("_", " ").title())

    # Opponent — guard against None from LEFT JOINs
    home = row.get("home_team") or ""
    away = row.get("away_team") or ""
    team = row.get("team") or ""
    if home and away:
        opp = f"vs {away}" if team == home else f"@ {home}"
    else:
        opp = ""

    # Result badge (settled picks)
    result = row.get("leg_result") or ""
    result_html = ""
    if result == "win":
        result_html = '<span style="float:right;color:#00d4a0;font-size:0.85rem;font-weight:700;">✓ WIN</span>'
    elif result == "loss":
        result_html = '<span style="float:right;color:#ff6b6b;font-size:0.85rem;font-weight:700;">✗ LOSS</span>'
    elif result == "push":
        result_html = '<span style="float:right;color:#ffd93d;font-size:0.8rem;font-weight:700;">PUSH</span>'

    # Form dots
    player_id  = int(row["player_id"])
    player_form = form_df[form_df["player_id"] == player_id].head(10)

    # player_form is most-recent-first; build over/None (push) per game.
    hits_l5, hits_l10 = [], []
    for _, fg in player_form.iterrows():
        a = float(fg["actual"])
        h = None if a == line else a > line   # exact line = push, not a hit/miss
        if len(hits_l10) < 10:
            hits_l10.append(h)
        if len(hits_l5) < 5:
            hits_l5.append(h)

    # Pad to 5 (missing oldest games sit on the left after reversal)
    while len(hits_l5) < 5:
        hits_l5.append(None)

    l5_hit  = sum(1 for h in hits_l5  if h is True)
    l10_hit = sum(1 for h in hits_l10 if h is True)
    l5_den  = sum(1 for h in hits_l5  if h is not None)
    l10_den = sum(1 for h in hits_l10 if h is not None)
    # From pick direction perspective (pushes already excluded from den)
    if direction == "under":
        l5_hit  = l5_den  - l5_hit
        l10_hit = l10_den - l10_hit

    # Render oldest→newest (most recent on the right), matching "Last 5 games"
    dots_html = form_dots_html(list(reversed(hits_l5)), direction)

    form_rate_html = ""
    if l5_den > 0:
        form_rate_html = (
            f'<div class="form-rate">'
            f'L5: <span>{l5_hit}/{l5_den}</span>'
            + (f' &nbsp; L10: <span>{l10_hit}/{l10_den}</span>' if l10_den >= 6 else '')
            + '</div>'
        )

    badge_cls  = "over" if direction == "over" else "under"
    badge_text = "OVER" if direction == "over" else "UNDER"
    inj_html  = f'<div class="inj-badge">⚠ +{inj:.0f} min from injuries</div>' if inj >= 15 else ""

    # The player's OWN injury status (warn before betting someone who's out/IL).
    # NULL joins come back as NaN (a truthy float), so coerce non-strings to "".
    _is = row.get("injury_status")
    inj_status = _is.strip() if isinstance(_is, str) else ""
    inj_status_html = ""
    if inj_status:
        _s = inj_status.lower()
        # Day-to-day / questionable etc = caution; everything else (Out, *-IL,
        # suspension, …) means they likely won't play = strong warning.
        _soft = any(k in _s for k in ("day-to-day", "day to day", "questionable",
                                      "doubtful", "probable", "gtd", "game-time"))
        cls = "warn" if _soft else "out"
        icon = "⚠" if _soft else "🚫"
        _in = row.get("injury_note")
        note = _in.strip() if isinstance(_in, str) else ""
        note = f" · {note[:48]}" if note else ""
        inj_status_html = (f'<div class="inj-status {cls}">{icon} {inj_status}'
                           f'<span class="inj-note">{note}</span></div>')
    kelly_pct = round(kelly * 100, 1)
    kelly_label = f"Kelly {kelly_pct}%" if kelly > 0 else ""
    kelly_row = (f'<div class="prob-row" style="margin-top:4px">'
                 f'<span class="prob-label">Kelly sizing</span>'
                 f'<span class="prob-value" style="color:#7c5ce8">{kelly_label}</span>'
                 f'</div>') if kelly > 0 else ""

    # Line movement signal
    lm = row.get("line_movement")
    lo = row.get("line_open")
    line_move_html = ""
    if lm is not None and lo is not None and abs(float(lm)) >= 0.05:
        mv = float(lm)
        # Positive = line moved up. For OVER picks that's bullish; for UNDER picks bearish.
        agrees = (mv > 0 and direction == "over") or (mv < 0 and direction == "under")
        arrow  = "↑" if mv > 0 else "↓"
        color  = "#00d4a0" if agrees else "#ff6b6b"
        label  = "sharp $" if agrees else "fading"
        line_move_html = (
            f'<div class="prob-row" style="margin-top:2px">'
            f'<span class="prob-label">Line moved</span>'
            f'<span class="prob-value" style="color:{color};font-size:0.8rem">'
            f'{arrow} {abs(mv):.1f} ({float(lo):g}→{line:g}) · {label}'
            f'</span></div>'
        )

    # Live in-game progress (only when the game is in progress)
    live_html = live_row_html(sport, row["stat_type"], line, direction, live) if live else ""

    # Synthesized rationale — the "why" behind the pick, from its strongest
    # signals (recent form, market edge, line movement). Keeps the dense card
    # data human-readable at a glance.
    why_bits = []
    if l5_den >= 3 and (l5_hit / l5_den) >= 0.6:
        why_bits.append(f"hit {badge_text} {l5_hit}/{l5_den} last 5")
    if edge >= 0.05:
        why_bits.append(f"+{edge * 100:.0f}% vs market")
    if lm is not None and lo is not None and abs(float(lm)) >= 0.05:
        _mv = float(lm)
        if (_mv > 0 and direction == "over") or (_mv < 0 and direction == "under"):
            why_bits.append("line moving your way")
    if not why_bits:
        why_bits.append(f"model {prob:.0%} confident")
    why_html = f'<div class="why">💡 {" · ".join(why_bits[:3])}</div>'

    # ⭐ recommended picks (clear their category cutoff) — starred + highlighted.
    is_rec = bool(row.get("_rec", False))
    rec_cls = " rec" if is_rec else ""
    star = '<span class="rec-star" title="Recommended — clears its category cutoff">⭐</span> ' if is_rec else ""

    # Projection + likely range — a confidence band, not just a point prob. The
    # model's predicted_mean is a Poisson rate for counting stats, so the 25–75%
    # quantiles give an honest "likely" range around the projection.
    proj_html = ""
    _pm = row.get("predicted_mean")
    if _pm is not None and pd.notna(_pm) and float(_pm) > 0:
        pm = float(_pm)
        try:
            from scipy.stats import poisson
            lo, hi = int(poisson.ppf(0.25, pm)), int(poisson.ppf(0.75, pm))
            proj_html = (f'<div class="proj-line">📊 Projection <b>{pm:.1f}</b>'
                         f' · likely <b>{lo}–{hi}</b></div>')
        except Exception:
            proj_html = f'<div class="proj-line">📊 Projection <b>{pm:.1f}</b></div>'

    # Weather chip (MLB) — wind blowing out drives offense (validated: 65% over
    # rate vs 43% calm/in). Only meaningful for hit/TB/HR-type props.
    weather_html = ""
    if sport == "mlb":
        _wt, _wo, _dome = row.get("wx_temp"), row.get("wx_wind_out"), row.get("wx_dome")
        if _dome:
            weather_html = '<div class="wx-chip">🏟️ dome (neutral)</div>'
        elif _wo is not None and pd.notna(_wo):
            wo = float(_wo)
            t = f"{float(_wt):.0f}°F · " if _wt is not None and pd.notna(_wt) else ""
            if wo >= 5:
                weather_html = f'<div class="wx-chip wx-out" title="Wind blowing out — boosts hits/TB/HR">{t}💨 wind out +{wo:.0f}</div>'
            elif wo <= -5:
                weather_html = f'<div class="wx-chip wx-in" title="Wind blowing in — suppresses offense">{t}🍃 wind in {wo:.0f}</div>'
            else:
                weather_html = f'<div class="wx-chip">{t}🍃 calm</div>'

    return _html(f"""
<div class="pick-card{rec_cls}">
  <div class="card-banner">
    <img src="{logo}"  class="team-logo"    onerror="this.style.display='none'">
    <img src="{photo}" class="player-photo" onerror="this.style.display='none'">
  </div>
  <div class="card-body">
    <div class="player-name">{result_html}{star}{row['player']}</div>
    <div class="team-stat">{row.get('team','')}{' · ' + opp if opp else ''} · {stat_label}</div>
    {weather_html}
    {inj_status_html}
    <div class="line-row">
      <span class="line-value">{line:g}</span>
      <span class="badge {badge_cls}">{badge_text}</span>
    </div>
    {proj_html}
    {live_html}
    <div class="prob-row">
      <span class="prob-label">Model confidence</span>
      <span class="prob-value">{prob:.0%}</span>
    </div>
    {kelly_row}
    {line_move_html}
    {edge_bar_html(edge)}
    <div class="form-section">
      <div class="form-label">Form · old → recent</div>
      {dots_html}
      {form_rate_html}
    </div>
    {why_html}
    {inj_html}
  </div>
</div>""")


def build_slate_card(picks_df: pd.DataFrame) -> str:
    """Build the recommended parlay slate card (recommended-tier picks only)."""
    if picks_df.empty:
        return ""

    # Only build the parlay from the recommended confidence tier — the backtest
    # shows legs below each category's cutoff are coin-flips that tank the joint
    # probability.
    qual = picks_df[_rec_mask(picks_df)]
    if qual.empty:
        return ""

    # Up to 4 UNCORRELATED legs: highest-confidence first, never two legs from
    # the same game in the same direction (those bust as a block when one game
    # runs hot/cold). Spreads the parlay across independent game outcomes.
    top = build_diversified_parlay(qual, max_legs=4)
    legs_html = ""
    stat_labels_slate = {
        "points": "Points", "rebounds": "Rebounds", "assists": "Assists",
        "threes_made": "3-PT Made", "pts_rebs_asts": "PRA",
        "pts_rebs": "P+R", "pts_asts": "P+A", "rebs_asts": "R+A",
        "blocks": "Blocks", "steals": "Steals", "blocks_steals": "Blk+Stl",
        "strikeouts_pitcher": "Strikeouts", "hits": "Hits",
        "total_bases": "Total Bases", "rbis": "RBIs", "home_runs": "Home Runs",
        "goals": "Goals", "saves": "Saves",
    }
    for _, row in top.iterrows():
        direction  = row["direction"]
        badge_cls  = "over" if direction == "over" else "under"
        badge_text = "OVER" if direction == "over" else "UNDER"
        stat_label = stat_labels_slate.get(row["stat_type"],
                                           row["stat_type"].replace("_", " ").title())
        sport_tag  = row.get("sport_code", "").upper()
        legs_html += f"""
<div class="slate-leg">
  <div>
    <div class="leg-player">{row['player']} <span style="color:#5a5f72;font-size:0.75rem;font-weight:400">{sport_tag}</span></div>
    <div class="leg-detail">{stat_label} · {float(row['line']):g} · {calibrate(float(row['model_prob'])):.0%}</div>
  </div>
  <span class="badge {badge_cls} leg-badge">{badge_text}</span>
</div>"""

    n     = min(4, len(top))
    mults = {2: "3×", 3: "5×", 4: "10×"}
    if n < 2:
        return ""  # Don't show slate with fewer than 2 picks
    joint   = float(top["model_prob"].astype(float).head(n).map(calibrate).prod())
    n_games = int(top["game_id"].nunique())
    return _html(f"""
<div class="slate-card">
  <div class="slate-title">⚡ Top {n}-Pick Slate · {mults[n]} payout</div>
  <div class="slate-meta">Diversified across {n_games} game{'s' if n_games != 1 else ''} · {joint:.0%} joint hit (if independent) · paper-tracking only, not betting advice</div>
  {legs_html}
</div>""")


# ── Sidebar controls: theme toggle + share link ─────────────────────────────
with st.sidebar:
    st.markdown("### ⚙️ Settings")
    _theme_qp = st.query_params.get("theme", "dark")
    _light = st.toggle("☀️ Light mode", value=(_theme_qp == "light"), key="theme")
    st.query_params["theme"] = "light" if _light else "dark"

    st.markdown("---")
    st.markdown("### 🔎 Player lookup")
    _tidx = load_team_index()      # full league rosters (all teams)
    _idx = load_player_index()     # players we have game data for, by recent team
    # Cascade: pick the League, then the Team (every team in the league), then a
    # Player we track on that team.
    _leagues = sorted(_tidx["sport"].unique())
    _lg = st.selectbox("League", ["—"] + [l.upper() for l in _leagues], key="lk_league")
    if _lg != "—":
        _lgc = _lg.lower()
        _teams = sorted(_tidx[_tidx["sport"] == _lgc]["team"].unique())
        _tm = st.selectbox("Team", ["—"] + _teams, key="lk_team")
        if _tm != "—":
            _players = sorted(_idx[(_idx["sport"] == _lgc)
                                   & (_idx["team"] == _tm)]["player"].unique())
            if _players:
                _pl = st.selectbox("Player", ["—"] + list(_players), key="lk_player")
                if _pl != "—" and st.button("View player →", use_container_width=True):
                    st.query_params["player"] = _pl
                    st.rerun()
            else:
                st.caption("No tracked players for this team yet.")

    st.markdown("### ⭐ Watchlist")
    _all_players = load_picked_players()
    _watch_qp = [w for w in (st.query_params.get("watch", "") or "").split(",") if w]
    _watch = st.multiselect("Follow players", _all_players,
                            default=[w for w in _watch_qp if w in _all_players], key="watch")
    if _watch:
        st.query_params["watch"] = ",".join(_watch)
    else:
        st.query_params.pop("watch", None)

    st.markdown("---")
    # A real (clickable, relative) link to the shareable results page — appends
    # ?view=results to the current URL.
    st.markdown("📣 **[Open your shareable results page →](?view=results)**")
    st.caption("Read-only record you can share — link copies the current URL + `?view=results`.")
    st.markdown("🛠️ **[Ops · cost & usage →](?view=ops)**")
    st.markdown("📜 **[Pick history · browse + export →](?view=history)**")
    st.markdown("⚖️ **[Compare players →](?view=compare)**")

# Light palette: override the design tokens (cards/components use the variables).
if _light:
    st.markdown("""<style>
    :root {
      --bg:#f4f5fa; --surface:#ffffff; --surface2:#eef0f6;
      --line:rgba(0,0,0,0.08); --line2:rgba(0,0,0,0.15);
      --txt:#13151c; --txt2:#475066; --txt3:#8b91a4;
    }
    [data-testid="stAppViewContainer"]{ background:
      radial-gradient(900px 500px at 12% -8%, rgba(124,92,255,0.07), transparent 60%),
      var(--bg); }
    section[data-testid="stSidebar"]{ background:#e9ebf2; }
    body, [class*="css"], [data-testid="stMarkdownContainer"] { color:var(--txt); }
    </style>""", unsafe_allow_html=True)

# ── Read-only drill-down views (shareable, st.stop) ──────────────────────────
if st.query_params.get("view") == "results":
    render_results_view()    # renders the record and st.stop()s
if st.query_params.get("view") == "ops":
    render_ops_view()        # cost/usage + dashboard health, st.stop()s
if st.query_params.get("view") == "history":
    render_history_view()    # filterable settled-pick browser + CSV, st.stop()s
if st.query_params.get("view") == "compare":
    render_compare_view()    # two players side by side, st.stop()s
_player_qp = st.query_params.get("player")
if isinstance(_player_qp, str) and _player_qp:
    render_player_view(_player_qp)   # player detail + st.stop()

# ── Header ────────────────────────────────────────────────────────────────────

st.markdown(
    '<h1 style="font-size:2rem;margin-bottom:0;font-weight:900;letter-spacing:-0.03em">'
    '<span style="background:linear-gradient(135deg,#9d7bff,#22d3ee);'
    '-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text">'
    '⚡ prop-edge</span></h1>',
    unsafe_allow_html=True)
st.markdown('<p style="color:#5f6678;margin-top:2px;margin-bottom:0.8rem;font-size:0.82rem;'
            'text-transform:uppercase;letter-spacing:0.08em">'
            'Research dashboard · paper-tracking only</p>', unsafe_allow_html=True)

# Compliance banner — research/paper-tracking framing, kept consistent + visible.
st.markdown('<div style="background:rgba(255,207,92,0.08);border:1px solid rgba(255,207,92,0.25);'
            'border-radius:10px;padding:9px 14px;margin-bottom:1.4rem;font-size:0.78rem;'
            'color:#c9b270;line-height:1.5">'
            '⚠️ <b>Research / paper-tracking only — not betting advice.</b> '
            'This project places no bets and touches no accounts; it tracks model predictions '
            'against publicly-visible PrizePicks lines. Intended for 21+ in jurisdictions where '
            'sports wagering is legal. All results are hypothetical.</div>',
            unsafe_allow_html=True)

df = load_todays_picks()

# 7-day rolling W/L for header (today's picks unsettled until evening)
_recent_7 = load_recent_picks(days=7)
_settled_7 = _recent_7[_recent_7["leg_result"].isin(["win", "loss"])]
wins_7    = (_settled_7["leg_result"] == "win").sum()
losses_7  = (_settled_7["leg_result"] == "loss").sum()
win_pct_7 = f"{wins_7/(wins_7+losses_7):.0%}" if (wins_7 + losses_7) else "—"
_valid_edges = df['market_edge'].dropna() if len(df) else pd.Series(dtype=float)
avg_edge  = f"{_valid_edges.mean():.1%}" if len(_valid_edges) else "—"
rec_count = int(_rec_mask(df).sum()) if len(df) else 0

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Today's Picks", len(df))
c2.metric("⭐ Recommended", rec_count,
          help="Picks clearing their category's tuned confidence cutoff "
               "(per-sport/stat; see category_cutoffs.json).")
c3.metric("Avg Edge", avg_edge)
c4.metric("7-Day W/L", f"{wins_7}W – {losses_7}L")
c5.metric("7-Day Win Rate", win_pct_7)


# ── Tabs ──────────────────────────────────────────────────────────────────────

tab_picks, tab_game, tab_perf, tab_soft, tab_recent = st.tabs(
    ["🃏 Today's Picks", "🏆 Game Predictions", "📊 Performance",
     "💰 Soft Lines", "📋 Recent Picks"]
)


# ══ TAB 1: Today's Picks ═════════════════════════════════════════════════════
with tab_picks:
    # Re-read the latest picks straight from the DB AND reset the filters. The
    # cloud pipeline logs picks for each sport at different times of the morning;
    # if the page first rendered when only MLB was in, the sport filter offered
    # only "mlb" and persisted ?sport=mlb to the URL — which then hides NBA/WNBA
    # even after their picks land. So a refresh must clear both the cached query
    # AND the persisted/sticky filters, otherwise a stale narrow filter survives.
    rc1, rc2 = st.columns([1, 4])
    with rc1:
        if st.button("🔄 Refresh picks", use_container_width=True,
                     help="Re-read the latest picks from the database and reset the "
                          "filters to show every sport. The pipeline adds picks "
                          "(MLB/NBA/WNBA/NHL) automatically each morning — tap this "
                          "to pull in anything logged after you opened the page."):
            # Drop the URL-persisted filters and the sticky widget state so the
            # sport/stat/direction selectors fall back to "all" on the rerun.
            for _k in ("sport", "stat", "dir", "rec"):
                st.query_params.pop(_k, None)
            for _k in ("sp", "st", "di", "rec"):
                st.session_state.pop(_k, None)
            st.cache_data.clear()
            st.rerun()
    with rc2:
        from datetime import datetime as _now_dt
        from zoneinfo import ZoneInfo as _ZI
        _now_pt = _now_dt.now(_ZI("America/Los_Angeles"))
        st.caption(f"Showing picks as of {_now_pt:%-I:%M %p %Z}. "
                   "New picks land each morning; refresh to pull the latest.")

    # ── Watchlist: today's picks for followed players (set in the sidebar) ────
    _wl_names = [w for w in (st.query_params.get("watch", "") or "").split(",") if w]
    if _wl_names and not df.empty:
        _wdf = df[df["player"].isin(_wl_names)]
        if not _wdf.empty:
            with st.expander(f"⭐ Watchlist — {len(_wdf)} pick(s) for "
                             f"{_wdf['player'].nunique()} followed player(s) today", expanded=True):
                for _, _r in _wdf.iterrows():
                    st.markdown(f"⭐ **{_r['player']}** — {_r['direction'].upper()} "
                                f"{float(_r['line']):g} {_r['stat_type']} "
                                f"<span style='color:#5f6678'>({calibrate(float(_r['model_prob'])):.0%})</span>",
                                unsafe_allow_html=True)

    if df.empty:
        st.info("No picks logged today yet. The daily cloud run posts them each "
                "morning — tap **🔄 Refresh picks** once it's done.")
    else:
        # Filters — persisted in the URL so they survive a reload / share link.
        _qp = st.query_params

        def _qp_default(name, allowed, fallback):
            raw = _qp.get(name)
            if raw is None:
                return fallback
            return [v for v in raw.split(",") if v in allowed]

        fc1, fc2, fc3 = st.columns(3)
        with fc1:
            sport_opts = sorted(df["sport_code"].unique())
            sport_sel  = st.multiselect("Sport", sport_opts,
                                        default=_qp_default("sport", sport_opts, sport_opts), key="sp")
        with fc2:
            stat_opts  = sorted(df["stat_type"].unique())
            stat_sel   = st.multiselect("Stat", stat_opts,
                                        default=_qp_default("stat", stat_opts, stat_opts), key="st")
        with fc3:
            dir_opts   = ["over", "under"]
            dir_sel    = st.multiselect("Direction", dir_opts,
                                        default=_qp_default("dir", dir_opts, dir_opts), key="di")

        rec_only = st.toggle(
            "⭐ Recommended only",
            value=_qp.get("rec", "0") == "1", key="rec",
            help="Off (default): show every pick, with ⭐ on the ones that clear "
                 "their per-category confidence cutoff (sorted first). On: hide "
                 "the rest. Cutoffs are auto-tuned from settled history.")

        # Persist filters to the URL only when they actually NARROW the slate.
        # If a selection equals every available option (e.g. an early-morning
        # MLB-only slate where "all" == just mlb), writing ?sport=mlb would later
        # hide NBA/WNBA picks once they land. Persisting only strict subsets means
        # an un-narrowed filter never poisons the URL and new sports auto-appear.
        def _persist(name, sel, opts):
            if sel and set(sel) != set(opts):
                _qp[name] = ",".join(sel)
            else:
                _qp.pop(name, None)
        _persist("sport", sport_sel, sport_opts)
        _persist("stat", stat_sel, stat_opts)
        _persist("dir", dir_sel, dir_opts)
        if rec_only:
            _qp["rec"] = "1"
        else:
            _qp.pop("rec", None)

        filtered = df[
            df["sport_code"].isin(sport_sel) &
            df["stat_type"].isin(stat_sel) &
            df["direction"].isin(dir_sel)
        ].reset_index(drop=True)
        # Flag recommended picks (clear their category cutoff) so the cards can
        # star them; show ALL picks by default, recommended sorted to the top.
        filtered["_rec"] = _rec_mask(filtered).values
        if rec_only:
            filtered = filtered[filtered["_rec"]].reset_index(drop=True)
            if filtered.empty:
                st.info("No picks clear their category cutoff today. "
                        "Toggle off to see all picks.")
        filtered = filtered.sort_values(
            ["_rec", "model_prob"], ascending=[False, False]).reset_index(drop=True)

        # Slate card — top picks across all sports by edge
        if not filtered.empty:
            st.markdown(build_slate_card(filtered), unsafe_allow_html=True)

        # ── Tail this slate — one-click copyable text of the RECOMMENDED picks ──
        _rec_df = filtered[filtered["_rec"]] if "_rec" in filtered.columns else filtered.iloc[0:0]
        if not _rec_df.empty:
            from props.utils.notify import format_slate
            from datetime import datetime as _td
            from zoneinfo import ZoneInfo as _tz
            _picks = [{"sport": r["sport_code"], "player": r["player"],
                       "direction": r["direction"], "line": r["line"],
                       "stat": r["stat_type"], "prob": calibrate(float(r["model_prob"]))}
                      for _, r in _rec_df.head(10).iterrows()]
            _par = build_diversified_parlay(_rec_df, max_legs=2)
            _parlay = ([{"player": p["player"], "prob": calibrate(float(p["model_prob"]))}
                        for _, p in _par.iterrows()] if len(_par) >= 2 else None)
            _label = _td.now(_tz("America/Los_Angeles")).strftime("%a %b %-d")
            with st.expander(f"📋 Tail this slate — copy {len(_picks)} recommended picks"):
                st.code(format_slate(_picks, _parlay, _label), language=None)
                st.caption("Tap the copy icon (top-right of the box) to grab the slate. "
                           "Paper-tracking only — not betting advice.")

        # ── Correlated same-game stacks ──────────────────────────────────────
        # A dominant pitcher (Ks OVER) and the opposing offense's UNDERs move
        # together — when he deals, the opposing batters fall short. Stacking
        # positively-correlated legs busts/wins as a block (higher joint hit rate
        # than independent legs), the opposite of the diversified parlay.
        _stacks = correlated_stacks(_rec_df) if not _rec_df.empty else []
        if _stacks:
            with st.expander(f"🔗 Correlated stacks ({len(_stacks)}) — pitcher Ks + opposing unders"):
                for s in _stacks[:5]:
                    st.markdown(
                        f"⚾ **{s['pitcher']}** OVER {s['p_line']:g} Ks  ＋  "
                        f"**{s['batter']}** UNDER {s['b_line']:g} {s['b_stat']}  "
                        f"<span style='color:#5f6678'>· joint ~{s['joint']:.0%} "
                        f"(they move together)</span>", unsafe_allow_html=True)
                st.caption("Positively-correlated: a pitcher dealing suppresses the "
                           "opposing offense, so these tend to hit (or miss) as a pair.")

        # Batch load form data per sport/stat group
        form_cache: dict[tuple, pd.DataFrame] = {}
        for (sport, stat), grp in filtered.groupby(["sport_code", "stat_type"]):
            pids = tuple(grp["player_id"].astype(int).unique())
            form_cache[(sport, stat)] = load_player_form(pids, stat, sport)

        # Live in-game stats (ESPN) for the sports on the board, keyed by player
        def _live_lookup():
            lk = {}
            for sp in filtered["sport_code"].unique():
                for nm, data in load_live_stats(sp).items():
                    lk[(sp, nm)] = data
            return lk

        any_live = bool(_live_lookup())
        if any_live:
            st.markdown(
                '<div style="color:#ff5d6c;font-weight:700;margin:4px 0 10px">'
                '🔴 LIVE games in progress — tracking picks in real time '
                '(auto-refreshes every 45s)</div>', unsafe_allow_html=True)

        # Card grid (3 per row) — wrapped in a fragment that re-fetches live
        # stats every 45s when games are on (no full-page reload).
        @st.fragment(run_every=("45s" if any_live else None))
        def _render_cards():
            live = _live_lookup() if any_live else {}
            # One responsive CSS grid instead of fixed 3-wide st.columns, so the
            # cards reflow (3-4 desktop → 1 on a phone) instead of squishing.
            cards = []
            for _, pick in filtered.iterrows():
                sport = pick["sport_code"]
                stat  = pick["stat_type"]
                fdf   = form_cache.get((sport, stat), pd.DataFrame())
                lv    = live.get((sport, _norm_name(pick["player"])))
                cards.append(build_pick_card(pick, fdf, lv))
            st.markdown('<div class="card-grid">' + "".join(cards) + "</div>",
                        unsafe_allow_html=True)

        _render_cards()


# ══ TAB 2: Game Predictions ══════════════════════════════════════════════════
def _game_card_html(home: str, away: str, home_wp: float,
                    margin: float, extra_html: str = "") -> str:
    away_wp    = 1 - home_wp
    fav        = home if home_wp >= 0.5 else away
    conf       = max(home_wp, away_wp)
    bar_home   = int(home_wp * 100)
    bar_away   = 100 - bar_home
    return _html(f"""
<div class="game-card">
  <div class="game-teams">{away} @ {home}</div>
  <div class="win-bar-bg">
    <div class="win-bar-home" style="width:{bar_home}%"></div>
    <div class="win-bar-away" style="width:{bar_away}%"></div>
  </div>
  <div class="team-prob">
    <span class="{'fav' if home_wp>=0.5 else 'dog'}">{home} {home_wp:.0%}</span>
    <span class="{'fav' if away_wp>home_wp else 'dog'}">{away} {away_wp:.0%}</span>
  </div>
  <div style="font-size:0.78rem;color:#8890a4;margin-top:6px">
    Model: <strong style="color:#fff">{fav}</strong> wins
    ({conf:.0%}) · Implied line: {fav} -{abs(margin):.1f}
  </div>
  {extra_html}
</div>""")


def _market_html(ms, mt, me, home, away) -> str:
    if ms is None:
        return ""
    mfav = home if ms <= 0 else away
    html = f'<div class="market-row"><span style="color:#8890a4">Market: {mfav} -{abs(ms):.1f} &nbsp;|&nbsp; O/U {mt}</span>'
    if me is not None:
        if abs(me) >= 0.10:
            bet = home if me > 0 else away
            line = f"+{abs(ms):.1f}" if (me < 0 and ms <= 0) else f"-{abs(ms):.1f}"
            html += f'<br><span class="rec-strong">▲ STRONG: {bet} {line} ({me:+.0%})</span>'
        elif abs(me) >= 0.05:
            bet = home if me > 0 else away
            html += f'<br><span class="rec-lean">→ LEAN: {bet} ({me:+.0%})</span>'
        else:
            html += f'<br><span class="rec-pass">PASS ({me:+.0%})</span>'
    return html + "</div>"


def _market_game_card_html(g: dict) -> str:
    """Game card driven by sportsbook moneyline (no winner model for this sport)."""
    home, away = g["home"], g["away"]
    home_wp = g.get("home_wp")
    spread, total = g.get("spread"), g.get("total")

    if home_wp is None:
        note = ("Live — odds closed" if g.get("state") == "in"
                else "Odds not yet posted")
        return _html(f"""
<div class="game-card">
  <div class="game-teams">{away} @ {home}</div>
  <div style="font-size:0.8rem;color:var(--txt3);margin-top:4px">{note}</div>
</div>""")

    away_wp  = 1 - home_wp
    fav      = home if home_wp >= 0.5 else away
    conf     = max(home_wp, away_wp)
    bar_home = int(home_wp * 100)
    line_txt = ""
    if spread is not None:
        line_txt = f" · {home if float(spread) <= 0 else away} {abs(float(spread)):g}"
    ou_txt = f" · O/U {total:g}" if total is not None else ""
    return _html(f"""
<div class="game-card">
  <div class="game-teams">{away} @ {home}</div>
  <div class="win-bar-bg">
    <div class="win-bar-home" style="width:{bar_home}%"></div>
    <div class="win-bar-away" style="width:{100 - bar_home}%"></div>
  </div>
  <div class="team-prob">
    <span class="{'fav' if home_wp>=0.5 else 'dog'}">{home} {home_wp:.0%}</span>
    <span class="{'fav' if away_wp>home_wp else 'dog'}">{away} {away_wp:.0%}</span>
  </div>
  <div style="font-size:0.78rem;color:#8890a4;margin-top:6px">
    Market favorite: <strong style="color:#fff">{fav}</strong> ({conf:.0%}){line_txt}{ou_txt}
  </div>
  <div style="font-size:0.66rem;color:var(--txt3);margin-top:6px;
              text-transform:uppercase;letter-spacing:0.05em">
    Sportsbook-implied · no model for this sport yet
  </div>
</div>""")


def _render_market_section(label: str, emoji: str, sport_path: str, date_str: str):
    st.markdown(f"### {emoji} {label}")
    games = load_market_games(sport_path, date_str)
    if not games:
        st.info(f"No {label} games today.")
        return
    cols = st.columns(min(len(games), 3))
    for i, g in enumerate(games):
        with cols[i % 3]:
            st.markdown(_market_game_card_html(g), unsafe_allow_html=True)


with tab_game:
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo
    # Use the Pacific date, not the server's (UTC on Railway) date — otherwise
    # the whole tab goes blank every evening once UTC rolls to tomorrow while
    # games are still stored under today's US date.
    _today = _dt.now(ZoneInfo("America/Los_Angeles")).date()

    # ── NBA ───────────────────────────────────────────────────────────────────
    st.markdown("### 🏀 NBA")
    game_preds = load_game_predictions_data()

    if game_preds.empty:
        try:
            from props.picks.predict_game import predict_games
            from props.picks.predict_today import (fetch_nba_schedule,
                                                    resolve_nba_external_to_internal_ids)
            from props.ingest.game_odds import fetch_nba_game_context, map_context_to_game_ids

            nba_raw   = fetch_nba_schedule(_today)
            nba_games = resolve_nba_external_to_internal_ids(nba_raw)
            espn_raw  = fetch_nba_game_context(_today)
            ctx_map   = map_context_to_game_ids(espn_raw, nba_games)
            preds     = predict_games(nba_games, _today, ctx_map)
            game_preds = pd.DataFrame(preds) if preds else pd.DataFrame()
        except Exception as e:
            _prediction_notice("NBA", e)
            game_preds = pd.DataFrame()

    if game_preds.empty:
        st.info("No NBA games today.")
    else:
        cols = st.columns(min(len(game_preds), 3))
        for i, (_, pred) in enumerate(game_preds.iterrows()):
            hwp  = float(pred.get("home_win_prob") or 0.5)
            margin = float(pred.get("implied_margin") or 0)
            home = pred.get("home_team", f"Team {pred.get('home_team_id','')}")
            away = pred.get("away_team", f"Team {pred.get('away_team_id','')}")
            mkt  = _market_html(pred.get("market_spread"), pred.get("market_total"),
                                pred.get("market_edge"), home, away)
            with cols[i % 3]:
                st.markdown(_game_card_html(home, away, hwp, margin, mkt),
                            unsafe_allow_html=True)

    # ── MLB ───────────────────────────────────────────────────────────────────
    # Read-only from games.context (persisted by the cron). No live schedule
    # fetch or LightGBM inference on render — that was the slow path / libgomp
    # risk this tab used to hit.
    st.markdown("### ⚾ MLB")
    mlb_df = load_mlb_game_predictions()
    if mlb_df.empty:
        st.info("No MLB predictions yet today — the cron computes these each morning.")
    else:
        cols = st.columns(3)
        for i, pred in enumerate(mlb_df.itertuples()):
            h_sp = pred.home_pitcher or "TBD"
            a_sp = pred.away_pitcher or "TBD"
            sp_html = (f'<div style="font-size:0.75rem;color:#8890a4;margin-top:4px">'
                       f'SP: {a_sp} vs {h_sp}</div>')
            with cols[i % 3]:
                st.markdown(_game_card_html(pred.home_team, pred.away_team,
                                            pred.home_win_prob, pred.implied_margin,
                                            sp_html),
                            unsafe_allow_html=True)

    # ── WNBA & NHL (market-implied — no winner model for these sports) ─────────
    _wnba_nhl_date = _today.strftime("%Y%m%d")
    _render_market_section("WNBA", "🏀", "basketball/wnba", _wnba_nhl_date)
    _render_market_section("NHL",  "🏒", "hockey/nhl",      _wnba_nhl_date)


# ══ TAB 3: Performance ═══════════════════════════════════════════════════════
with tab_perf:
    all_picks = load_all_settled_picks()
    hist      = load_historical_summary()

    if all_picks.empty:
        st.info("No settled picks yet — check back after tonight's games.")
    else:
        won  = (all_picks["leg_result"] == "win").sum()
        lost = (all_picks["leg_result"] == "loss").sum()
        total_decided = won + lost
        win_pct = won / total_decided if total_decided else 0

        # ── Top metrics ──────────────────────────────────────────────────────
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Settled Picks", total_decided)
        c2.metric("Win Rate", f"{win_pct:.1%}")
        c3.metric("Record", f"{won}W – {lost}L")
        be = 0.577
        c4.metric("vs Breakeven", f"{win_pct - be:+.1%}",
                  delta_color="normal" if win_pct >= be else "inverse")
        roi_2pick = win_pct**2 * 3 - 1
        c5.metric("2-pick ROI (sim)", f"{roi_2pick:+.1%}")

        # ── Recommended-tier vs all (proves the per-category cutoffs) ─────────
        rec = all_picks[_rec_mask(all_picks).values
                        & (all_picks["leg_result"].isin(["win", "loss"]))]
        if len(rec) >= 10:
            rec_wr = (rec["leg_result"] == "win").mean()
            st.success(
                f"⭐ **Recommended tier (per-category cutoffs):** "
                f"**{rec_wr:.1%}** win rate over {len(rec)} settled picks "
                f"(vs {win_pct:.1%} for all) · 2-pick ROI {rec_wr**2*3-1:+.0%}. "
                "This is the tier surfaced by default on Today's Picks.")

        # ── ROI by parlay size (realistic PrizePicks power-play payouts) ──────
        # Per-leg win rate from the recommended tier (the slate you'd actually
        # play); ROI assumes independent legs at that rate.
        p_leg = rec_wr if len(rec) >= 10 else win_pct
        PARLAY_MULT = {2: 3.0, 3: 5.0, 4: 10.0}  # PrizePicks power play
        with st.expander(f"🎰 ROI by parlay size (at {p_leg:.0%} per-leg win rate)", expanded=True):
            roi_rows = []
            for n, mult in PARLAY_MULT.items():
                joint = p_leg ** n
                roi = joint * mult - 1
                roi_rows.append({
                    "Parlay": f"{n}-pick",
                    "Payout": f"{mult:g}x",
                    "All-hit chance": f"{joint:.1%}",
                    "Expected ROI": f"{roi:+.1%}",
                })
            st.dataframe(pd.DataFrame(roi_rows), hide_index=True, use_container_width=True)
            st.caption("Power-play (all legs must hit). More legs = higher payout "
                       "but the all-hit chance compounds down — the +EV sweet spot "
                       "is usually the smallest parlay. Assumes independent legs.")

        with st.expander("🎚️ Active confidence cutoffs (auto-tuned per category)"):
            st.caption(
                "Lowest model-confidence at which each category's settled win "
                f"rate clears the {CUTOFFS.get('breakeven', 0.577):.1%} 2-pick "
                "breakeven (Wilson lower bound). Recomputed from history every 6h.")
            _ct = CUTOFFS.get("sports", {})
            if _ct:
                _rows = [{
                    "Sport": sp.upper(),
                    "Cutoff": f"{v['cutoff']:.0%}",
                    "Settled n": v["n"],
                    "Win rate": f"{v['win_rate']:.1%}" if v.get("win_rate") else "—",
                    "Status": v.get("status", ""),
                } for sp, v in sorted(_ct.items())]
                st.dataframe(pd.DataFrame(_rows), hide_index=True,
                             use_container_width=True)
            _st_cells = CUTOFFS.get("stats", {})
            if _st_cells:
                st.caption("Stat-level overrides (where a single stat has enough "
                           "history to tune on its own):")
                st.dataframe(pd.DataFrame([{
                    "Sport·Stat": k.replace("|", " · "),
                    "Cutoff": f"{v['cutoff']:.0%}",
                    "Settled n": v["n"],
                    "Win rate": f"{v['win_rate']:.1%}" if v.get("win_rate") else "—",
                } for k, v in sorted(_st_cells.items())]),
                hide_index=True, use_container_width=True)

        # ── Closing Line Value ────────────────────────────────────────────────
        st.divider()
        st.subheader("📈 Closing Line Value")
        clv_df = all_picks.copy()
        if "line_close" in clv_df.columns:
            clv_df["clv"] = clv_df.apply(
                lambda r: clv_points(r["line"], r["line_close"], r["direction"]), axis=1)
            clv_df = clv_df[clv_df["clv"].notna()]
        else:
            clv_df = clv_df.iloc[0:0]
        if len(clv_df) < 5:
            st.caption("Not enough picks with a captured closing line yet — CLV "
                       "builds as the daily pipeline records closing lines.")
        else:
            beat = (clv_df["clv"] > 0).mean()
            avg_clv = clv_df["clv"].mean()
            moved = (clv_df["clv"] != 0).mean()
            # The validation: do positive-CLV picks actually win more? If yes, the
            # model has real timing edge, not just variance.
            wl = clv_df[clv_df["leg_result"].isin(["win", "loss"])]
            pos_wr = (wl[wl["clv"] > 0]["leg_result"] == "win").mean() if (wl["clv"] > 0).any() else float("nan")
            neg_wr = (wl[wl["clv"] <= 0]["leg_result"] == "win").mean() if (wl["clv"] <= 0).any() else float("nan")
            cc1, cc2, cc3 = st.columns(3)
            cc1.metric("Beat the close", f"{beat:.0%}",
                       help="Share of picks that got a better number than the line's "
                            "closing value. >50% = you're picking before the market moves.")
            cc2.metric("Avg CLV", f"{avg_clv:+.2f} pts",
                       help="Average line points gained vs the close. Positive = sharp timing.")
            cc3.metric("Win rate: +CLV vs −CLV",
                       f"{pos_wr:.0%} / {neg_wr:.0%}" if pos_wr == pos_wr and neg_wr == neg_wr else "—",
                       help="If +CLV picks win more than −CLV picks, CLV is predicting wins "
                            "= genuine edge (not luck).")
            st.caption(f"Over {len(clv_df)} picks with a captured close — but only "
                       f"{moved:.0%} of standard lines moved at all (PrizePicks lines "
                       "are sticky, so CLV is a weaker signal here than at a sharp "
                       "book). Avg CLV near zero = neutral timing; watch whether "
                       "+CLV picks keep out-winning −CLV ones as the sample grows.")

        # ── Sharp-market CLV (the real signal — DK/FD move, PrizePicks doesn't) ─
        sclv = load_sharp_clv()
        if len(sclv) >= 5:
            st.markdown("**Sharp-market CLV** — vs the DraftKings/FanDuel close (in "
                        "win-probability points; the sharp line actually moves).")
            beat_s = (sclv["clv"] > 0).mean()
            avg_s  = sclv["clv"].mean()
            pos = sclv[sclv["clv"] > 0]; neg = sclv[sclv["clv"] <= 0]
            pos_wr = (pos["leg_result"] == "win").mean() if len(pos) else float("nan")
            neg_wr = (neg["leg_result"] == "win").mean() if len(neg) else float("nan")
            sc1, sc2, sc3 = st.columns(3)
            sc1.metric("Beat the sharp close", f"{beat_s:.0%}",
                       help="Share of picks whose side the sharp market moved TOWARD after "
                            "we picked — i.e. we got a better number than it closed at.")
            sc2.metric("Avg sharp CLV", f"{avg_s*100:+.1f} pp",
                       help="Average win-probability points gained vs the sharp close. "
                            "Positive = genuine timing edge against a market that moves.",
                       delta_color="normal" if avg_s >= 0 else "inverse")
            sc3.metric("Win rate: +CLV vs −CLV",
                       f"{pos_wr:.0%} / {neg_wr:.0%}" if pos_wr == pos_wr and neg_wr == neg_wr else "—",
                       help="If +sharp-CLV picks win more, the model has real edge the sharp "
                            "market later agrees with.")
            st.caption(f"Over {len(sclv)} picks with both a pick-time and closing sharp prob "
                       "(captured by the intraday refresh near tip-off). This is the "
                       "gold-standard edge signal — builds as the sample grows.")

        # ── Paper bankroll / ROI ──────────────────────────────────────────────
        st.divider()
        st.subheader("💰 Paper P&L")
        bk_scope = st.radio("Bets included", ["Recommended", "All picks"],
                            horizontal=True, key="bk")
        bk_src = (all_picks[_rec_mask(all_picks).values]
                  if bk_scope.startswith("Recommended") else all_picks)
        curve, m = simulate_bankroll(bk_src)
        if not m or m["n"] < 5:
            st.info("Not enough settled picks in this scope for a P&L curve yet.")
        else:
            b1, b2, b3, b4 = st.columns(4)
            b1.metric("Units won", f"{m['units']:+.1f}u",
                      delta_color="normal" if m["units"] >= 0 else "inverse")
            b2.metric("ROI / yield", f"{m['yield']:+.1%}", "per unit staked",
                      delta_color="normal" if m["yield"] >= 0 else "inverse")
            b3.metric("Settled bets", f"{m['n']}  ({m['win_rate']:.0%} W)")
            b4.metric("Max drawdown", f"-{m['max_dd']:.1f}u", delta_color="off")
            chart = curve.to_frame("Cumulative P&L (units)")
            chart["Breakeven"] = 0.0
            st.line_chart(chart, height=240)
            st.caption("Flat 1u/leg · win pays +0.73u, loss −1u (per-leg equivalent of a "
                       "2-pick 3× parlay, decimal √3) · paper-tracking only, not betting advice.")

        # ── Model backtest history ────────────────────────────────────────────
        bt_trend = load_backtest_trend()
        if not bt_trend.empty:
            st.divider()
            st.subheader("Backtest history — model accuracy over time")
            st.caption("Each row = one backtest run (after model retrains or weekly Monday run)")
            bt_display = bt_trend.copy()
            bt_display.columns = [c.replace("_"," ").title() for c in bt_display.columns]
            st.dataframe(bt_display, use_container_width=True, hide_index=True)

            nba_bt = bt_trend[bt_trend["sport"] == "nba"].sort_values("date")
            if len(nba_bt) >= 2:
                nba_bt = nba_bt.set_index("date")
                nba_bt["breakeven"] = 57.7
                st.line_chart(nba_bt[["win_pct","breakeven"]], height=180)
                st.caption("NBA backtest win rate vs 57.7% breakeven across runs")

        # ── Daily walk-forward backtest (on the system's own settled picks) ─────
        wf = load_daily_backtest()
        if not wf.empty:
            st.divider()
            st.subheader("🧪 Daily walk-forward backtest")
            latest = wf.iloc[0]
            w1, w2, w3, w4 = st.columns(4)
            w1.metric("Rec-tier win rate",
                      f"{latest['rec_win_pct']:.1f}%",
                      f"{latest['rec_win_pct'] - 57.7:+.1f} vs breakeven",
                      delta_color="normal" if latest['rec_win_pct'] >= 57.7 else "inverse")
            w2.metric("2-pick ROI", f"{latest['rec_roi_pct']:+.0f}%",
                      delta_color="normal" if latest['rec_roi_pct'] >= 0 else "inverse")
            w3.metric("Rec sample", f"{int(latest['rec_w'])}–{int(latest['rec_l'])}",
                      f"{int(latest['window_days'])}d window")
            w4.metric("Brier", f"{latest['brier']:.3f}", "lower = sharper",
                      delta_color="off")

            if len(wf) >= 2:
                chart = wf.sort_values("run_date").set_index("run_date")[["rec_win_pct"]].copy()
                chart["breakeven"] = 57.7
                st.line_chart(chart, height=180)
                st.caption("Recommended-tier win rate per daily run vs 57.7% breakeven "
                           "(rolling window of settled picks).")

            # Counterfactual cutoff findings from the latest run
            detail = latest.get("detail") or {}
            if isinstance(detail, str):
                import json as _json
                try:
                    detail = _json.loads(detail)
                except Exception:
                    detail = {}
            material = [f for f in detail.get("cutoff_sweep", []) if f.get("material")]
            if material:
                with st.expander(f"⚠️ Cutoff-fit findings ({len(material)}) — auto-tuner check"):
                    rows_disp = []
                    for f in material:
                        lw = (f"{f['live_winrate']*100:.0f}%"
                              if f.get("live_winrate") is not None else "suppressed")
                        rows_disp.append({
                            "Bucket": f"{f['sport']} {f['stat']}",
                            "Live cutoff": f"{f['live']:.2f} ({lw}, n={f['live_n']})",
                            "Optimal": f"{f['opt']['cutoff']:.2f} "
                                       f"({f['opt']['winrate']*100:.0f}%, n={f['opt']['n']})",
                            "Opt ROI": f"{f['opt']['ev']*100:+.0f}%",
                        })
                    st.dataframe(pd.DataFrame(rows_disp), use_container_width=True, hide_index=True)
                    st.caption("Where a different cutoff would have made more money on the "
                               "settled window. The auto-tuner moves toward these as data accrues.")

        st.divider()

        # ── Win rate over time ────────────────────────────────────────────────
        st.subheader("Win rate over time")
        all_picks["pick_date"] = pd.to_datetime(all_picks["pick_date"])
        weekly = (all_picks[all_picks["leg_result"].isin(["win","loss"])]
                  .set_index("pick_date")
                  .resample("W")["leg_result"]
                  .agg(wins=lambda s: (s=="win").sum(),
                       total=lambda s: len(s)))
        weekly = weekly[weekly["total"] >= 3].copy()
        if not weekly.empty:
            weekly["win_pct"] = weekly["wins"] / weekly["total"] * 100
            weekly["breakeven"] = 57.7
            st.line_chart(weekly[["win_pct", "breakeven"]], height=200)
            st.caption("Win rate % per week vs 57.7% breakeven (2-pick parlay)")
        else:
            st.info("Need at least 2 weeks of picks for this chart.")

        st.divider()

        # ── Edge threshold analysis ───────────────────────────────────────────
        st.subheader("Does higher edge = higher win rate?")
        buckets = []
        for lo, hi in [(0, 5), (5, 10), (10, 15), (15, 25), (25, 100)]:
            mask = (all_picks["market_edge"].abs() * 100 >= lo) & \
                   (all_picks["market_edge"].abs() * 100 < hi) & \
                   (all_picks["leg_result"].isin(["win", "loss"]))
            sub = all_picks[mask]
            if len(sub) >= 3:
                wr = (sub["leg_result"] == "win").mean()
                buckets.append({"Edge bucket": f"{lo}-{hi}%", "N": len(sub),
                                 "Win rate": round(wr * 100, 1),
                                 "vs Breakeven": f"{wr*100 - 57.7:+.1f}%"})
        if buckets:
            st.dataframe(pd.DataFrame(buckets), use_container_width=True,
                         hide_index=True)
        else:
            st.info("Need more settled picks across edge buckets.")

        st.divider()

        # ── Calibration: model says X%, does X% actually hit? ────────────────
        st.subheader("Model calibration — is the model's confidence accurate?")
        cal_data = all_picks[all_picks["leg_result"].isin(["win","loss"])].copy()
        cal_data["prob_bin"] = pd.cut(cal_data["model_prob"],
                                       bins=[0,.45,.50,.55,.60,.65,.70,.80,1.0],
                                       labels=["<45%","45-50%","50-55%","55-60%",
                                               "60-65%","65-70%","70-80%",">80%"])
        cal = (cal_data.groupby("prob_bin", observed=True)
               .agg(n=("leg_result","count"),
                    avg_prob=("model_prob","mean"),
                    actual_hit=("leg_result", lambda s: (s=="win").mean()))
               .reset_index())
        cal = cal[cal["n"] >= 3].copy()
        if not cal.empty:
            cal["Model said"] = (cal["avg_prob"] * 100).round(1).astype(str) + "%"
            cal["Actual hit"] = (cal["actual_hit"] * 100).round(1).astype(str) + "%"
            cal["Gap"] = ((cal["actual_hit"] - cal["avg_prob"]) * 100).round(1)
            cal["Gap str"] = cal["Gap"].apply(lambda x: f"{x:+.1f}%")
            st.dataframe(
                cal[["prob_bin","n","Model said","Actual hit","Gap str"]].rename(
                    columns={"prob_bin":"Prob bucket","n":"N",
                             "Gap str":"Gap (actual − model)"}),
                use_container_width=True, hide_index=True)
            st.caption("Negative gap = model is overconfident (says 70% but only hits 55%)")
        else:
            st.info("Need more settled picks for calibration analysis.")

        st.divider()

        # ── Edge leaderboard — best & worst buckets ──────────────────────────
        st.subheader("🏆 Edge leaderboard")
        if not hist.empty:
            _lb = hist[hist["picks"] >= 8].copy()
            if not _lb.empty:
                _lb["Bucket"] = (_lb["sport_code"].str.upper() + " " + _lb["stat_type"]
                                 + " " + _lb["direction"].str.upper())
                _lb["Record"] = _lb.apply(
                    lambda r: f"{int(r['wins'])}–{int(r['losses'])}", axis=1)
                _lb["Win %"] = _lb["win_pct"].map(lambda x: f"{x:.0f}%")
                _lb["vs 57.7%"] = _lb["win_pct"].map(lambda x: f"{x-57.7:+.0f}%")
                _lb = _lb.sort_values("win_pct", ascending=False)
                cols = ["Bucket", "Record", "Win %", "vs 57.7%"]
                lc1, lc2 = st.columns(2)
                with lc1:
                    st.caption("🔥 Hottest (min 8 settled)")
                    st.dataframe(_lb.head(6)[cols], use_container_width=True, hide_index=True)
                with lc2:
                    st.caption("🧊 Coldest")
                    st.dataframe(_lb.tail(6).iloc[::-1][cols],
                                 use_container_width=True, hide_index=True)
            else:
                st.caption("Not enough settled picks per bucket yet (need ≥8).")
        st.divider()

        # ── Breakdown by stat type ────────────────────────────────────────────
        st.subheader("Win rate by stat type")
        if not hist.empty:
            display = hist[hist["picks"] >= 5].copy()
            display["Win %"] = display["win_pct"].apply(
                lambda x: f"{x:.1f}%" if pd.notna(x) else "—")
            display["Record"] = display.apply(
                lambda r: f"{int(r['wins'])}W-{int(r['losses'])}L", axis=1)
            display["vs Break"] = display["win_pct"].apply(
                lambda x: f"{x-57.7:+.1f}%" if pd.notna(x) else "—")
            st.dataframe(
                display[["sport_code","stat_type","direction","picks",
                          "Record","Win %","vs Break","avg_prob","avg_edge"]]
                .rename(columns={"sport_code":"Sport","stat_type":"Stat",
                                  "direction":"Dir","picks":"N",
                                  "avg_prob":"Avg Prob","avg_edge":"Avg Edge",
                                  "vs Break":"vs 57.7%"}),
                use_container_width=True, hide_index=True)

        st.divider()

        # ── Recommendations ───────────────────────────────────────────────────
        st.subheader("Model recommendations")
        if not hist.empty and total_decided >= 10:
            recs_keep, recs_drop, recs_watch = [], [], []
            for _, row in hist[hist["picks"] >= 5].iterrows():
                label = f"{row['sport_code'].upper()} {row['stat_type']} {row['direction'].upper()}"
                wp = float(row["win_pct"]) if pd.notna(row["win_pct"]) else 0
                if wp >= 60:
                    recs_keep.append(f"✅ **{label}** — {wp:.1f}% win rate ({int(row['wins'])}W-{int(row['losses'])}L)")
                elif wp < 50:
                    recs_drop.append(f"🚫 **{label}** — {wp:.1f}% win rate, below 50% ({int(row['wins'])}W-{int(row['losses'])}L)")
                elif 50 <= wp < 57.7:
                    recs_watch.append(f"⚠️ **{label}** — {wp:.1f}% win rate, below breakeven")

            if recs_keep:
                st.markdown("**Keep betting — above breakeven:**")
                for r in recs_keep: st.markdown(r)
            if recs_watch:
                st.markdown("**Watch — close to breakeven:**")
                for r in recs_watch: st.markdown(r)
            if recs_drop:
                st.markdown("**Consider removing — consistently below 50%:**")
                for r in recs_drop: st.markdown(r)
            if not recs_keep and not recs_drop and not recs_watch:
                st.info("Need more picks per category for recommendations (5+ per stat type).")
        else:
            st.info(f"Need 10+ settled picks for recommendations. Have {total_decided} so far.")


# ══ TAB 4: Soft Lines ════════════════════════════════════════════════════════
with tab_soft:
    st.subheader("💰 Soft lines vs the sharp market")
    st.caption("PrizePicks lines the sharp books (DK/FD) price as **+EV, independent "
               "of our model** — the line-shopping edge. We recover the sharp market's "
               "implied projection and re-price it at the PrizePicks line; a side whose "
               "market-implied win % clears the 57.7% 2-pick breakeven is a soft line.")
    _soft = load_soft_lines()
    if _soft.empty:
        st.info("No soft lines computed yet today. The finder runs each morning "
                "(needs live sharp odds); check back after the slate is priced.")
    else:
        _pos = _soft[_soft["edge"] >= 0.02].copy()
        st.metric("Soft lines found (≥+2% edge)", len(_pos),
                  f"of {len(_soft)} props with sharp coverage")
        if _pos.empty:
            st.info("Sharp market agrees with PrizePicks today — no soft lines clearing "
                    "breakeven. (The market is efficient most nights; this is normal.)")
        else:
            disp = _pos.copy()
            disp["Pick"] = (disp["best_side"].str.upper() + " " +
                            disp["pp_line"].map(lambda x: f"{x:g}") + " " + disp["stat_type"])
            disp["Market win %"] = (disp["best_prob"] * 100).map(lambda x: f"{x:.0f}%")
            disp["Edge"] = (disp["edge"] * 100).map(lambda x: f"+{x:.1f}%")
            disp["Sharp line"] = disp["sharp_line"].map(lambda x: f"{x:g}")
            disp = disp.rename(columns={"player_name": "Player", "sport_code": "Sport"})
            st.dataframe(
                disp[["Sport", "Player", "Pick", "Sharp line", "Market win %", "Edge"]],
                use_container_width=True, hide_index=True)
            st.caption("Edge = market-implied win % − 57.7% breakeven. Independent of the "
                       "model picks in the other tabs — paper-tracking only, not advice.")


# ══ TAB 5: Recent Picks ══════════════════════════════════════════════════════
with tab_recent:
    st.markdown("##### 🔎 Browse settled & recent picks")
    days = st.slider("Days back", 1, 60, 7)
    recent = load_recent_picks(days)

    if recent.empty:
        st.info("No picks in this range.")
    else:
        # Filters — sport / stat / direction / result + a historical date picker.
        f1, f2, f3, f4, f5 = st.columns(5)
        with f1:
            sp_sel = st.multiselect("Sport", sorted(recent["sport_code"].unique()), key="rb_sp")
        with f2:
            stat_sel = st.multiselect("Stat", sorted(recent["stat_type"].unique()), key="rb_st")
        with f3:
            dir_sel = st.multiselect("Direction", ["over", "under"], key="rb_di")
        with f4:
            res_sel = st.multiselect("Result", ["win", "loss", "push"], key="rb_re")
        with f5:
            _dates = ["All dates"] + [str(d) for d in
                                      sorted(recent["date"].unique(), reverse=True)]
            day_sel = st.selectbox("Date", _dates, key="rb_date")

        view = recent.copy()
        if sp_sel:   view = view[view["sport_code"].isin(sp_sel)]
        if stat_sel: view = view[view["stat_type"].isin(stat_sel)]
        if dir_sel:  view = view[view["direction"].isin(dir_sel)]
        if res_sel:  view = view[view["leg_result"].isin(res_sel)]
        if day_sel != "All dates":
            view = view[view["date"].astype(str) == day_sel]

        # Settled-record summary for the current filter.
        _wl = view[view["leg_result"].isin(["win", "loss"])]
        if len(_wl):
            _w = int((_wl["leg_result"] == "win").sum())
            st.caption(f"{len(view)} picks shown · settled **{_w}–{len(_wl)-_w}** "
                       f"({_w/len(_wl)*100:.0f}%) vs 57.7% breakeven")
        else:
            st.caption(f"{len(view)} picks shown (none settled in this filter)")

        def result_color(val):
            if val == "win":   return "color: #00d4a0; font-weight:600"
            if val == "loss":  return "color: #ff6b6b; font-weight:600"
            if val == "push":  return "color: #ffd93d"
            return ""

        st.dataframe(
            view.style.map(result_color, subset=["leg_result"]),
            use_container_width=True, hide_index=True, height=560
        )

st.markdown('<p style="color:#3a3d4e;font-size:0.72rem;text-align:center;margin-top:2rem">'
            'prop-edge · paper-tracking only · not betting advice</p>',
            unsafe_allow_html=True)
