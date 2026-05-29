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

st.set_page_config(page_title="prop-edge", layout="wide",
                   initial_sidebar_state="collapsed",
                   page_icon="⚡")

# ── CSS ─────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* Base */
[data-testid="stAppViewContainer"] { background: #0f1117; }
[data-testid="stHeader"] { background: #0f1117; }
section[data-testid="stSidebar"] { background: #13161e; }
.block-container { padding-top: 1.5rem !important; }

/* Typography */
h1, h2, h3 { color: #ffffff !important; }
p, label, div { color: #c8cdd8; }

/* Metric cards */
[data-testid="metric-container"] {
    background: #1a1d2e; border-radius: 12px;
    padding: 12px 16px; border: 1px solid #2a2d3e;
}
[data-testid="stMetricValue"] { color: #ffffff !important; font-size: 1.8rem !important; }
[data-testid="stMetricLabel"] { color: #8890a4 !important; }

/* Tabs */
.stTabs [data-baseweb="tab-list"] { background: #13161e; border-radius: 10px; padding: 4px; }
.stTabs [data-baseweb="tab"] { color: #8890a4; border-radius: 8px; padding: 8px 20px; }
.stTabs [data-baseweb="tab"][aria-selected="true"] {
    background: #5932d9 !important; color: #fff !important;
}

/* Pick cards */
.pick-card {
    background: #1a1d2e;
    border: 1px solid #2a2d3e;
    border-radius: 16px;
    padding: 0;
    margin-bottom: 16px;
    overflow: hidden;
    transition: transform 0.15s ease, box-shadow 0.15s ease;
}
.pick-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 8px 24px rgba(89,50,217,0.25);
}
.card-banner {
    position: relative;
    height: 110px;
    background: linear-gradient(135deg, #1e2235 0%, #252840 100%);
    overflow: hidden;
}
.card-banner .team-logo {
    position: absolute; top: 10px; right: 10px;
    width: 44px; height: 44px; object-fit: contain; opacity: 0.9;
}
.card-banner .player-photo {
    position: absolute; bottom: 0; left: 12px;
    height: 105px; width: auto; object-fit: cover;
    object-position: top; border-radius: 8px 8px 0 0;
}
.card-body { padding: 12px 14px 14px 14px; }
.player-name {
    font-size: 0.95rem; font-weight: 700;
    color: #ffffff; margin-bottom: 2px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.team-stat { font-size: 0.75rem; color: #8890a4; margin-bottom: 10px; }
.line-row {
    display: flex; align-items: center;
    justify-content: space-between; margin-bottom: 8px;
}
.line-value { font-size: 1.6rem; font-weight: 800; color: #ffffff; }
.badge {
    font-size: 0.7rem; font-weight: 700; letter-spacing: 0.08em;
    padding: 4px 10px; border-radius: 20px;
}
.badge.over  { background: rgba(0,212,160,0.15); color: #00d4a0; border: 1px solid #00d4a0; }
.badge.under { background: rgba(255,77,77,0.15);  color: #ff6b6b; border: 1px solid #ff6b6b; }
.prob-row {
    display: flex; align-items: center;
    justify-content: space-between; margin-bottom: 10px;
}
.prob-label { font-size: 0.72rem; color: #8890a4; }
.prob-value { font-size: 0.85rem; font-weight: 700; color: #ffffff; }
.edge-bar-bg {
    height: 4px; background: #2a2d3e; border-radius: 2px; margin-bottom: 10px;
}
.edge-bar-fill {
    height: 4px; border-radius: 2px;
    background: linear-gradient(90deg, #5932d9, #7c5ce8);
}
.form-section { margin-top: 8px; }
.form-label { font-size: 0.7rem; color: #8890a4; margin-bottom: 4px; }
.form-dots { display: flex; gap: 5px; align-items: center; margin-bottom: 4px; }
.dot {
    width: 22px; height: 22px; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-size: 0.65rem; font-weight: 700;
}
.dot.hit   { background: rgba(0,212,160,0.2); color: #00d4a0; border: 1.5px solid #00d4a0; }
.dot.miss  { background: rgba(255,107,107,0.2); color: #ff6b6b; border: 1.5px solid #ff6b6b; }
.dot.empty { background: #2a2d3e; color: #5a5f72; border: 1.5px solid #3a3d4e; }
.form-rate { font-size: 0.72rem; color: #8890a4; }
.form-rate span { color: #ffffff; font-weight: 600; }
.inj-badge {
    font-size: 0.68rem; color: #ffd93d; background: rgba(255,217,61,0.12);
    border: 1px solid rgba(255,217,61,0.3); border-radius: 6px;
    padding: 2px 7px; margin-top: 6px; display: inline-block;
}
.kelly-row { font-size: 0.7rem; color: #8890a4; margin-top: 4px; }
.kelly-row span { color: #7c5ce8; font-weight: 600; }

/* Slate card */
.slate-card {
    background: linear-gradient(135deg, #1a1d2e, #1e2235);
    border: 1px solid #5932d9;
    border-radius: 16px; padding: 20px 24px; margin-bottom: 24px;
}
.slate-title { font-size: 1.1rem; font-weight: 700; color: #fff; margin-bottom: 4px; }
.slate-meta  { font-size: 0.78rem; color: #8890a4; margin-bottom: 14px; }
.slate-leg {
    display: flex; align-items: center; justify-content: space-between;
    padding: 8px 0; border-bottom: 1px solid #2a2d3e;
}
.slate-leg:last-child { border-bottom: none; }
.leg-player { font-size: 0.85rem; font-weight: 600; color: #fff; }
.leg-detail { font-size: 0.78rem; color: #8890a4; }
.leg-badge  { font-size: 0.7rem; padding: 2px 8px; border-radius: 12px; }

/* Game prediction card */
.game-card {
    background: #1a1d2e; border: 1px solid #2a2d3e;
    border-radius: 16px; padding: 20px; margin-bottom: 16px;
}
.game-teams { font-size: 1.05rem; font-weight: 700; color: #fff; margin-bottom: 12px; }
.win-bar-bg {
    height: 8px; background: #2a2d3e; border-radius: 4px;
    margin: 8px 0; overflow: hidden; display: flex;
}
.win-bar-home { height: 8px; background: #5932d9; border-radius: 4px 0 0 4px; }
.win-bar-away { height: 8px; background: #8890a4; border-radius: 0 4px 4px 0; }
.team-prob { display: flex; justify-content: space-between; font-size: 0.8rem; }
.team-prob .fav { color: #ffffff; font-weight: 700; }
.team-prob .dog { color: #8890a4; }
.market-row {
    display: flex; justify-content: space-between; align-items: center;
    margin-top: 12px; padding-top: 12px; border-top: 1px solid #2a2d3e;
    font-size: 0.8rem;
}
.rec-strong { color: #00d4a0; font-weight: 700; }
.rec-lean   { color: #ffd93d; font-weight: 700; }
.rec-pass   { color: #8890a4; }
</style>
""", unsafe_allow_html=True)


# ── Helpers ──────────────────────────────────────────────────────────────────

def player_photo_url(external_id: str, sport: str) -> str:
    if not external_id or external_id.startswith("pp_"):
        return "https://cdn.nba.com/headshots/nba/latest/1040x760/fallback.png"
    if sport == "nba":
        return f"https://cdn.nba.com/headshots/nba/latest/1040x760/{external_id}.png"
    if sport == "mlb":
        return (f"https://img.mlbstatic.com/mlb-photos/image/upload/"
                f"d_people:generic:headshot:67:current.png/w_213,q_auto:best"
                f"/v1/people/{external_id}/headshot/67/current")
    return ""


def team_logo_url(team_ext_id: str, sport: str) -> str:
    if not team_ext_id:
        return ""
    if sport == "nba":
        return f"https://cdn.nba.com/logos/nba/{team_ext_id}/global/L/logo.svg"
    if sport == "mlb":
        return f"https://www.mlbstatic.com/team-logos/{team_ext_id}.svg"
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
            g.status          AS game_status,
            ht.abbreviation   AS home_team,
            at.abbreviation   AS away_team
        FROM picks pk
        JOIN players p   USING (player_id)
        JOIN teams   t   ON t.team_id = p.current_team_id
        JOIN games   g   USING (game_id)
        JOIN prop_lines pl ON pl.line_id = pk.line_id
        LEFT JOIN teams ht ON ht.team_id = g.home_team_id
        LEFT JOIN teams at ON at.team_id = g.away_team_id
        WHERE pk.picked_at::date = CURRENT_DATE
        ORDER BY COALESCE(pk.market_edge, pk.edge) DESC
    """
    return pd.read_sql(text(sql), engine)


@st.cache_data(ttl=300)
def load_player_form(player_ids: tuple, stat_type: str, sport: str):
    """Return last 10 actuals per player for a given stat_type."""
    if not player_ids:
        return pd.DataFrame()
    sql = """
        SELECT pg.player_id, g.game_date,
               COALESCE((pg.stats->>:stat)::float, 0) AS actual
        FROM player_games pg
        JOIN games g USING (game_id)
        WHERE pg.player_id = ANY(:pids)
          AND g.sport_code  = :sport
          AND g.status = 'final'
          AND g.game_date < CURRENT_DATE
        ORDER BY pg.player_id, g.game_date DESC
    """
    df = pd.read_sql(text(sql), engine,
                     params={"pids": list(player_ids), "stat": stat_type,
                             "sport": sport})
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
        WHERE pk.leg_result IS NOT NULL
        GROUP BY g.sport_code, pk.stat_type, pk.direction
        ORDER BY picks DESC
    """
    return pd.read_sql(text(sql), engine)


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
            pl.line_value               AS line
        FROM picks pk
        JOIN games g     USING (game_id)
        JOIN prop_lines pl ON pl.line_id = pk.line_id
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
        WHERE pk.picked_at >= CURRENT_DATE - :days
        ORDER BY pk.picked_at DESC
    """
    return pd.read_sql(text(sql), engine, params={"days": days})


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
        WHERE g.game_date = CURRENT_DATE
          AND g.sport_code = 'nba'
          AND g.context ? 'home_win_prob'
        ORDER BY g.game_id
    """
    return pd.read_sql(text(sql), engine)


# ── Pick card builder ─────────────────────────────────────────────────────────

def build_pick_card(row, form_df: pd.DataFrame) -> str:
    sport   = row["sport_code"]
    photo   = player_photo_url(row.get("player_ext_id", ""), sport)
    logo    = team_logo_url(row.get("team_ext_id", ""), sport)
    direction = row["direction"]
    line    = float(row["line"])
    prob    = float(row["model_prob"])
    edge    = float(row.get("market_edge") or row.get("edge") or 0)
    kelly   = float(row.get("kelly") or 0)
    inj     = float(row.get("injury_flag") or 0)

    # Stat type display name
    stat_labels = {
        "points": "Points", "rebounds": "Rebounds", "assists": "Assists",
        "threes_made": "3-PT Made", "pts_rebs_asts": "PRA",
        "pts_rebs": "P+R", "pts_asts": "P+A", "rebs_asts": "R+A",
        "blocks": "Blocks", "steals": "Steals", "turnovers": "Turnovers",
        "strikeouts_pitcher": "Strikeouts", "hits": "Hits",
        "total_bases": "Total Bases", "rbis": "RBIs",
    }
    stat_label = stat_labels.get(row["stat_type"], row["stat_type"].replace("_", " ").title())

    # Opponent
    home, away = row.get("home_team", ""), row.get("away_team", "")
    opp = f"vs {away}" if row.get("team") == home else f"@ {home}" if home else ""

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
    stat_type  = row["stat_type"]
    player_form = form_df[form_df["player_id"] == player_id].head(10)

    hits_l5, hits_l10 = [], []
    for _, fg in player_form.iterrows():
        h = fg["actual"] > line
        if len(hits_l10) < 10:
            hits_l10.append(h)
        if len(hits_l5) < 5:
            hits_l5.append(h)

    # Pad to 5
    while len(hits_l5) < 5:
        hits_l5.append(None)

    l5_hit  = sum(1 for h in hits_l5  if h is True)
    l10_hit = sum(1 for h in hits_l10 if h is True)
    l5_den  = sum(1 for h in hits_l5  if h is not None)
    l10_den = sum(1 for h in hits_l10 if h is not None)
    # From pick direction perspective
    if direction == "under":
        l5_hit  = l5_den  - l5_hit
        l10_hit = l10_den - l10_hit

    dots_html = form_dots_html(hits_l5, direction)

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
    kelly_pct = round(kelly * 100, 1)
    kelly_label = f"Kelly {kelly_pct}%" if kelly > 0 else ""
    kelly_row = (f'<div class="prob-row" style="margin-top:4px">'
                 f'<span class="prob-label">Kelly sizing</span>'
                 f'<span class="prob-value" style="color:#7c5ce8">{kelly_label}</span>'
                 f'</div>') if kelly > 0 else ""

    return f"""
<div class="pick-card">
  <div class="card-banner">
    <img src="{logo}"  class="team-logo"    onerror="this.style.display='none'">
    <img src="{photo}" class="player-photo" onerror="this.style.display='none'">
  </div>
  <div class="card-body">
    <div class="player-name">{result_html}{row['player']}</div>
    <div class="team-stat">{row.get('team','')}{' · ' + opp if opp else ''} · {stat_label}</div>
    <div class="line-row">
      <span class="line-value">{line:g}</span>
      <span class="badge {badge_cls}">{badge_text}</span>
    </div>
    <div class="prob-row">
      <span class="prob-label">Model confidence</span>
      <span class="prob-value">{prob:.0%}</span>
    </div>
    {kelly_row}
    {edge_bar_html(edge)}
    <div class="form-section">
      <div class="form-label">Last 5 games vs line</div>
      {dots_html}
      {form_rate_html}
    </div>
    {inj_html}
  </div>
</div>"""


def build_slate_card(picks_df: pd.DataFrame) -> str:
    """Build the recommended parlay slate card."""
    if picks_df.empty:
        return ""

    # Simple slate: top 4 picks by edge
    top = picks_df.head(4)
    legs_html = ""
    for _, row in top.iterrows():
        direction  = row["direction"]
        badge_cls  = "over" if direction == "over" else "under"
        badge_text = "OVER" if direction == "over" else "UNDER"
        stat_label = row["stat_type"].replace("_", " ").title()
        legs_html += f"""
<div class="slate-leg">
  <div>
    <div class="leg-player">{row['player']}</div>
    <div class="leg-detail">{stat_label} · {float(row['line']):g} · {row['model_prob']:.0%}</div>
  </div>
  <span class="badge {badge_cls} leg-badge">{badge_text}</span>
</div>"""

    n      = min(4, len(top))
    mults  = {2: "3x", 3: "5x", 4: "10x"}
    return f"""
<div class="slate-card">
  <div class="slate-title">⚡ Today's Recommended {n}-Pick Slate ({mults.get(n,'?')} payout)</div>
  <div class="slate-meta">Ranked by model edge · Confirm injury reports before submitting</div>
  {legs_html}
</div>"""


# ── Header ────────────────────────────────────────────────────────────────────

st.markdown('<h1 style="font-size:1.8rem;margin-bottom:0">⚡ prop-edge</h1>',
            unsafe_allow_html=True)
st.markdown('<p style="color:#8890a4;margin-top:0;margin-bottom:1.5rem;font-size:0.85rem">'
            'Research dashboard · paper-tracking only</p>', unsafe_allow_html=True)

df = load_todays_picks()

settled   = df[df["leg_result"].notna()]
wins      = (settled["leg_result"] == "win").sum()
total_set = len(settled[settled["leg_result"].isin(["win","loss"])])
win_pct   = f"{wins/total_set:.0%}" if total_set else "—"
avg_edge  = f"{df['market_edge'].mean():.1%}" if len(df) else "—"
above_be  = (df["model_prob"] >= 0.577).sum()

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Today's Picks", len(df))
c2.metric("Above Breakeven", above_be)
c3.metric("Avg Edge", avg_edge)
c4.metric("Settled W/L", f"{wins}/{total_set - wins}")
c5.metric("Win Rate", win_pct)


# ── Tabs ──────────────────────────────────────────────────────────────────────

tab_picks, tab_game, tab_perf, tab_recent = st.tabs(
    ["🃏 Today's Picks", "🏆 Game Predictions", "📊 Performance", "📋 Recent Picks"]
)


# ══ TAB 1: Today's Picks ═════════════════════════════════════════════════════
with tab_picks:
    if df.empty:
        st.info("No picks logged today. Daily cron runs at 9 AM.")
    else:
        # Filters
        fc1, fc2, fc3 = st.columns(3)
        with fc1:
            sport_opts = sorted(df["sport_code"].unique())
            sport_sel  = st.multiselect("Sport", sport_opts, default=sport_opts, key="sp")
        with fc2:
            stat_opts  = sorted(df["stat_type"].unique())
            stat_sel   = st.multiselect("Stat", stat_opts, default=stat_opts, key="st")
        with fc3:
            dir_opts   = ["over", "under"]
            dir_sel    = st.multiselect("Direction", dir_opts, default=dir_opts, key="di")

        filtered = df[
            df["sport_code"].isin(sport_sel) &
            df["stat_type"].isin(stat_sel) &
            df["direction"].isin(dir_sel)
        ].reset_index(drop=True)

        # Slate card
        nba_picks = filtered[filtered["sport_code"] == "nba"]
        if not nba_picks.empty:
            st.markdown(build_slate_card(nba_picks), unsafe_allow_html=True)

        # Batch load form data per sport/stat group
        form_cache: dict[tuple, pd.DataFrame] = {}
        for (sport, stat), grp in filtered.groupby(["sport_code", "stat_type"]):
            pids = tuple(grp["player_id"].astype(int).unique())
            form_cache[(sport, stat)] = load_player_form(pids, stat, sport)

        # Card grid (3 per row)
        cols_per_row = 3
        rows = [filtered.iloc[i:i+cols_per_row]
                for i in range(0, len(filtered), cols_per_row)]

        for row_picks in rows:
            cols = st.columns(cols_per_row)
            for col, (_, pick) in zip(cols, row_picks.iterrows()):
                sport  = pick["sport_code"]
                stat   = pick["stat_type"]
                fdf    = form_cache.get((sport, stat), pd.DataFrame())
                with col:
                    st.markdown(build_pick_card(pick, fdf), unsafe_allow_html=True)


# ══ TAB 2: Game Predictions ══════════════════════════════════════════════════
def _game_card_html(home: str, away: str, home_wp: float,
                    margin: float, extra_html: str = "") -> str:
    away_wp    = 1 - home_wp
    fav        = home if home_wp >= 0.5 else away
    conf       = max(home_wp, away_wp)
    bar_home   = int(home_wp * 100)
    bar_away   = 100 - bar_home
    return f"""
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
</div>"""


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


with tab_game:
    from datetime import date as _date
    _today = _date.today()

    # ── NBA ───────────────────────────────────────────────────────────────────
    st.markdown("### 🏀 NBA")
    game_preds = load_game_predictions_data()

    if game_preds.empty:
        try:
            from props.picks.predict_game import predict_games
            from props.picks.predict_today import (fetch_nba_schedule,
                                                    resolve_nba_external_to_internal_ids)
            from props.ingest.game_odds import fetch_nba_game_context, map_context_to_game_ids
            from props.utils.db import session_scope
            from sqlalchemy import text as sqlt

            nba_raw   = fetch_nba_schedule(_today)
            nba_games = resolve_nba_external_to_internal_ids(nba_raw)
            espn_raw  = fetch_nba_game_context(_today)
            ctx_map   = map_context_to_game_ids(espn_raw, nba_games)
            preds     = predict_games(nba_games, _today, ctx_map)
            game_preds = pd.DataFrame(preds) if preds else pd.DataFrame()
        except Exception as e:
            st.warning(f"NBA predictions unavailable: {e}")
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
    st.markdown("### ⚾ MLB")
    try:
        from props.picks.predict_today import (fetch_todays_schedule_with_pitchers,
                                                resolve_external_to_internal_ids)
        from props.picks.predict_mlb_game import predict_mlb_games
        from props.utils.db import session_scope
        from sqlalchemy import text as sqlt

        mlb_sched = fetch_todays_schedule_with_pitchers(_today)
        mlb_sched = resolve_external_to_internal_ids(mlb_sched)
        mlb_games_valid = [g for g in mlb_sched if g.get("game_id") and g.get("home_team_id")]

        with session_scope() as s:
            trows = s.execute(sqlt(
                "SELECT team_id, COALESCE(city || ' ', '') || name FROM teams WHERE sport_code='mlb'"
            )).all()
        mlb_names = {r[0]: r[1] for r in trows}

        mlb_preds = predict_mlb_games(mlb_games_valid, _today) if mlb_games_valid else []
    except Exception as e:
        st.warning(f"MLB predictions unavailable: {e}")
        mlb_preds = []

    if not mlb_preds:
        st.info("No MLB games today.")
    else:
        cols = st.columns(3)
        for i, pred in enumerate(mlb_preds):
            hwp    = pred["home_win_prob"]
            margin = pred["implied_margin"]
            home   = mlb_names.get(pred["home_team_id"], f"Team {pred['home_team_id']}")
            away   = mlb_names.get(pred["away_team_id"], f"Team {pred['away_team_id']}")
            h_sp   = pred.get("home_pitcher", "TBD")
            a_sp   = pred.get("away_pitcher", "TBD")
            sp_html = (f'<div style="font-size:0.75rem;color:#8890a4;margin-top:4px">'
                       f'SP: {a_sp} vs {h_sp}</div>')
            with cols[i % 3]:
                st.markdown(_game_card_html(home, away, hwp, margin, sp_html),
                            unsafe_allow_html=True)


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


# ══ TAB 4: Recent Picks ══════════════════════════════════════════════════════
with tab_recent:
    days = st.slider("Days back", 1, 30, 7)
    recent = load_recent_picks(days)

    if recent.empty:
        st.info("No picks in this range.")
    else:
        # Color code results
        def result_color(val):
            if val == "win":   return "color: #00d4a0; font-weight:600"
            if val == "loss":  return "color: #ff6b6b; font-weight:600"
            if val == "push":  return "color: #ffd93d"
            return ""

        st.dataframe(
            recent.style.map(result_color, subset=["leg_result"]),
            use_container_width=True, hide_index=True, height=600
        )

st.markdown('<p style="color:#3a3d4e;font-size:0.72rem;text-align:center;margin-top:2rem">'
            'prop-edge · paper-tracking only · not betting advice</p>',
            unsafe_allow_html=True)
