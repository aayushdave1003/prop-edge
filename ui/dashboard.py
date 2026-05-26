"""Streamlit dashboard for prop-edge picks.

Run: streamlit run ui/dashboard.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import pandas as pd
from sqlalchemy import text
from props.utils.db import engine


st.set_page_config(page_title="prop-edge", layout="wide")
st.title("prop-edge")
st.caption("Research dashboard - paper-tracking only, not betting advice")


@st.cache_data(ttl=60)
def load_todays_picks():
    sql = """
        SELECT pk.pick_id,
               g.sport_code,
               p.full_name AS player,
               pk.stat_type,
               pl.line_value AS line,
               pk.direction,
               pk.model_prob AS model_probability,
               pk.edge,
               pk.picked_at,
               g.external_id AS game,
               g.status AS game_status,
               pk.leg_result,
               pk.actual_value
        FROM picks pk
        JOIN players p USING (player_id)
        JOIN games g USING (game_id)
        JOIN prop_lines pl ON pl.line_id = pk.line_id
        WHERE pk.picked_at::date = CURRENT_DATE
        ORDER BY pk.edge DESC
    """
    return pd.read_sql(text(sql), engine)


@st.cache_data(ttl=300)
def load_historical_summary():
    sql = """
        SELECT g.sport_code,
               pk.stat_type,
               pk.direction,
               COUNT(*) AS picks,
               COUNT(*) FILTER (WHERE pk.leg_result = 'win') AS wins,
               COUNT(*) FILTER (WHERE pk.leg_result = 'loss') AS losses,
               COUNT(*) FILTER (WHERE pk.leg_result = 'push') AS pushes,
               ROUND(100.0 * COUNT(*) FILTER (WHERE pk.leg_result = 'win')
                     / NULLIF(COUNT(*) FILTER (WHERE pk.leg_result IN ('win', 'loss')), 0), 1) AS win_pct
        FROM picks pk
        JOIN games g USING (game_id)
        WHERE pk.leg_result IS NOT NULL
        GROUP BY g.sport_code, pk.stat_type, pk.direction
        ORDER BY g.sport_code, pk.stat_type, pk.direction
    """
    return pd.read_sql(text(sql), engine)


# Main picks table
df = load_todays_picks()
total = len(df)

col1, col2, col3, col4 = st.columns(4)
col1.metric("Today's Picks", total)
col2.metric("MLB", len(df[df["sport_code"] == "mlb"]))
col3.metric("NBA", len(df[df["sport_code"] == "nba"]))
col4.metric("Avg Edge", f"{df['edge'].mean():.3f}" if total else "-")

# Filters
st.divider()
left, right = st.columns(2)
with left:
    sport_filter = st.multiselect(
        "Sport",
        options=sorted(df["sport_code"].unique().tolist()),
        default=sorted(df["sport_code"].unique().tolist()),
    )
with right:
    stat_filter = st.multiselect(
        "Stat type",
        options=sorted(df["stat_type"].unique().tolist()),
        default=sorted(df["stat_type"].unique().tolist()),
    )

filtered = df[
    df["sport_code"].isin(sport_filter)
    & df["stat_type"].isin(stat_filter)
]

# Picks table
st.subheader(f"Picks ({len(filtered)})")
display = filtered[[
    "player", "sport_code", "stat_type", "line", "direction",
    "model_probability", "edge", "game_status", "leg_result"
]].copy()
display.columns = [
    "Player", "Sport", "Stat", "Line", "Pick",
    "Model Prob", "Edge", "Game Status", "Result"
]
display["Model Prob"] = display["Model Prob"].astype(float).round(3)
display["Edge"] = display["Edge"].astype(float).round(3)
st.dataframe(display, use_container_width=True, hide_index=True)

# Historical results
st.divider()
st.subheader("Settled paper-tracking results")
hist = load_historical_summary()
if hist.empty:
    st.info("No settled picks yet. Run settle_picks after games finish.")
else:
    st.dataframe(hist, use_container_width=True, hide_index=True)
    
    # Pretty bar chart of win rates
    chart_df = hist[hist["picks"] >= 10].copy()
    chart_df["label"] = chart_df["sport_code"] + " " + chart_df["stat_type"] + " " + chart_df["direction"]
    if not chart_df.empty:
        st.bar_chart(chart_df.set_index("label")["win_pct"], height=300)

st.divider()
st.caption("Auto-refresh every 60s. Picks refresh hourly via cron.")
