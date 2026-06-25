"""Display-label maps shared by the web API. The frontend renders these verbatim
(it never derives labels), so all human-facing naming lives here."""

# internal stat_type -> short PrizePicks-style display label
STAT_LABELS = {
    # NBA / WNBA
    "points": "Points",
    "rebounds": "Rebounds",
    "assists": "Assists",
    "fg3_made": "3PT Made",
    "threes_made": "3PT Made",
    "blocks": "Blocks",
    "steals": "Steals",
    "turnovers": "Turnovers",
    "pts_rebs_asts": "PRA",
    "pts_rebs": "Pts+Rebs",
    "pts_asts": "Pts+Asts",
    "rebs_asts": "Rebs+Asts",
    "blocks_steals": "Blks+Stls",
    # MLB batter
    "hits": "Hits",
    "total_bases": "Total Bases",
    "home_runs": "Home Runs",
    "rbis": "RBIs",
    "runs": "Runs",
    "singles": "Singles",
    "doubles": "Doubles",
    "walks": "Walks",
    "stolen_bases": "Stolen Bases",
    "strikeouts_batter": "Batter Ks",
    "hits_runs_rbis": "H+R+RBI",
    # MLB pitcher
    "strikeouts_pitcher": "Pitcher Ks",
    "earned_runs_allowed": "Earned Runs",
    "hits_allowed": "Hits Allowed",
    "outs": "Outs",
    "walks_allowed": "Walks Allowed",
}

# sport_code -> league display label
LEAGUE_LABELS = {
    "nba": "NBA",
    "wnba": "WNBA",
    "mlb": "MLB",
    "nhl": "NHL",
    "soccer": "Soccer",
    "nfl": "NFL",
    "cfb": "CFB",
    "cbb": "CBB",
}


def stat_label(stat_type: str) -> str:
    if stat_type in STAT_LABELS:
        return STAT_LABELS[stat_type]
    return stat_type.replace("_", " ").title()


def league_label(sport_code: str) -> str:
    return LEAGUE_LABELS.get(sport_code, (sport_code or "").upper())
