import re
import pandas as pd
from sqlalchemy import (Integer, String, Float, Boolean, Date, DateTime)


SHOT_EVENT_TYPES = {
    "goal",
    "penalty goal",
    "own goal",
    "attempt blocked",
    "attempt missed",
    "miss",
    "attempt saved",
    "penalty saved",
    "penalty miss",
    "post",
}

MATCH_EVENT_PRIORITY = {
    "goal": 0,
    "penalty goal": 0,
    "own goal": 0,
    "substitution": 1,
    "yellow": 2,
    "red": 3,
}

TEAM_SUB_RE = re.compile(r"^\s*substitution\s*,\s*([^\.]+?)\s*(?:\.|$)", re.IGNORECASE)
TEAM_PAREN_RE = re.compile(r"\(([^)]+)\)")

TEAM_MATCH_ENDPOINT_COLS = [
    "formation_used",
    "possession_percentage",
    "touches",
    "touches_in_final_third",
    "touches_in_opp_box",
    "total_pass",
    "accurate_pass",
    "total_back_zone_pass",
    "accurate_back_zone_pass",
    "total_fwd_zone_pass",
    "accurate_fwd_zone_pass",
    "total_final_third_passes",
    "total_long_balls",
    "accurate_long_balls",
    "long_pass_own_to_opp",
    "long_pass_own_to_opp_success",
    "total_cross",
    "accurate_cross",
    "corner_taken",
    "total_corners_intobox",
    "accurate_corners_intobox",
    "total_throws",
    "carries",
    "progressive_carries",
    "total_scoring_att",
    "ontarget_scoring_att",
    "attempts_obox",
    "attempts_ibox",
    "attempts_conceded_ibox",
    "attempts_conceded_obox",
    "shot_off_target",
    "first_half_goals",
    "subs_made",
    "subs_goals",
    "goals_conceded_ibox",
    "goals_conceded_obox",
    "big_chance_missed",
    "big_chance_created",
    "big_chance_scored",
    "goal_assist",
    "total_tackle",
    "won_tackle",
    "interception",
    "ball_recovery",
    "dispossessed",
    "duel_won",
    "duel_lost",
    "aerial_won",
    "aerial_lost",
    "total_clearance",
    "defensive_actions",
    "blocked_scoring_att",
    "outfielder_block",
    "won_contest",
    "total_contest",
    "error_lead_to_goal",
    "big_chance_saves",
    "fk_foul_lost",
    "ppda",
    "penalty_faced",
    "pen_goals_conceded",
    "poss_won_att_3rd",
    "poss_won_mid_3rd",
    "poss_won_def_3rd",
    "total_yel_card",
    "total_red_card",
    "total_offside",
    "pts_gained_losing_pos",
    "pts_dropped_winning_pos",
]

TEAM_MATCH_FLOAT_COLS = {"possession_percentage", "ppda"}

TEAM_MATCH_PLAYER_COLS = [
    "non_penalty_goals",
    "penalties_scored",
    "penalties_missed",
    "own_goals",
    "saves",
    "defensive_contribution",
    "xg",
    "xa",
    "xga",
    "npxg",
]

TEAM_MATCH_OUTPUT_COLS = [
    "fixture_id",
    "season_id",
    "team_id",
    "is_home",
    *TEAM_MATCH_ENDPOINT_COLS,
    "goals_scored",
    "goals_conceded",
    "result",
    "points",
    *TEAM_MATCH_PLAYER_COLS,
]

PLAYER_SEASON_ENDPOINT_COLS = [
    "appearances",
    "game_started",
    "total_sub_on",
    "total_sub_off",
    "mins_played",
    "goals",
    "goals_openplay",
    "goal_assist",
    "goal_assist_deadball",
    "total_att_assist",
    "big_chance_created",
    "big_chance_missed",
    "total_scoring_att",
    "ontarget_scoring_att",
    "total_pass",
    "accurate_pass",
    "total_final_third_passes",
    "successful_final_third_passes",
    "blocked_pass",
    "total_long_balls",
    "accurate_long_balls",
    "total_cross",
    "accurate_cross",
    "corner_taken",
    "final_third_entries",
    "pen_area_entries",
    "carries",
    "progressive_carries",
    "touches",
    "touches_in_final_third",
    "touches_in_opp_box",
    "total_through_ball",
    "total_tackle",
    "won_tackle",
    "times_tackled",
    "duel_won",
    "duel_lost",
    "interception_won",
    "challenge_lost",
    "ball_recovery",
    "dispossessed",
    "outfielder_block",
    "total_clearance",
    "aerial_won",
    "aerial_lost",
    "fouls",
    "was_fouled",
    "yellow_card",
    "second_yellow",
    "red_card",
    "total_offside",
    "penalty_conceded",
    "penalty_won",
    "clean_sheet",
    "total_keeper_sweeper",
    "accurate_keeper_sweeper",
    "poss_won_att_3rd",
    "accurate_lauches",
    "accurate_keeper_throws",
    "keeper_throws",
    "total_high_claim",
    "goal_kicks",
    "accurate_goal_kicks",
    "poss_won_def_3rd",
    "poss_won_mid_3rd",
    "penalty_faced",
    "pen_goals_conceded",
    "good_high_claim",
    "goals_conceded"
]

PLAYER_FIXTURE_DERIVED_COLS = [
    "non_penalty_goals",
    "penalties_missed",
    "penalties_scored",
    "saves",
    "own_goals",
    "defensive_contribution",
    "xg",
    "npxg",
    "xa",
]

def normalize_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass

    text = str(value).lower().replace("&", "and")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def extract_textstream_events(payload) -> list[dict]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []

    events = payload.get("events")
    if isinstance(events, dict):
        content = events.get("content")
        if isinstance(content, list):
            return content

    content = payload.get("content")
    if isinstance(content, list):
        return content

    return []


def format_minute(raw_value) -> str | None:
    if raw_value is None:
        return None
    if isinstance(raw_value, dict):
        raw_value = raw_value.get("label") or raw_value.get("minute") or raw_value.get("value")
    try:
        if pd.isna(raw_value):
            return None
    except TypeError:
        pass

    text = str(raw_value).strip().replace("’", "'").rstrip("'")
    if not text:
        return None

    added = re.match(r"^0*(\d+)\s*\+\s*(\d+)$", text)
    if added:
        return f"{int(added.group(1))}+{int(added.group(2))}'"

    normal = re.match(r"^0*(\d+)$", text)
    if normal:
        return normal.group(1)

    return text


def minute_sort_key(minute_text: str | None) -> tuple[int, int]:
    if minute_text is None:
        return (10**9, 0)

    text = str(minute_text).strip().replace("’", "'").rstrip("'")

    added = re.match(r"^(\d+)\+(\d+)$", text)
    if added:
        return (int(added.group(1)), int(added.group(2)))

    normal = re.match(r"^(\d+)$", text)
    if normal:
        return (int(normal.group(1)), 0)

    return (10**9, 0)


def clean_player_id(value, known_player_ids: set[int]) -> int | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass

    try:
        pid = int(value)
    except (TypeError, ValueError):
        return None

    return pid if pid in known_player_ids else None


def build_fixture_team_alias_map(
    dim_teams: pd.DataFrame,
    home_team_id: int,
    away_team_id: int,
) -> dict[str, int]:
    subset = dim_teams.loc[
        dim_teams["team_id"].isin([home_team_id, away_team_id]),
        ["team_id", "team_name", "short_name"],
    ].copy()

    alias_to_team_id: dict[str, int] = {}
    for row in subset.itertuples(index=False):
        team_id = int(row.team_id)
        for alias in (row.team_name, row.short_name):
            alias_norm = normalize_text(alias)
            if alias_norm:
                alias_to_team_id[alias_norm] = team_id

    return alias_to_team_id


def resolve_team_id_from_text(text: str, alias_to_team_id: dict[str, int]) -> int | None:
    text_norm = normalize_text(text)
    if not text_norm:
        return None

    for alias, team_id in sorted(alias_to_team_id.items(), key=lambda item: len(item[0]), reverse=True):
        if alias and alias in text_norm:
            return int(team_id)

    return None


def infer_event_team_id(ev_type: str, text: str, alias_to_team_id: dict[str, int]) -> int | None:
    ev_type_norm = normalize_text(ev_type)
    text = text or ""

    if "substitution" in ev_type_norm:
        match = TEAM_SUB_RE.search(text)
        if not match:
            return None
        return resolve_team_id_from_text(match.group(1).strip(), alias_to_team_id)

    match = TEAM_PAREN_RE.search(text)
    if not match:
        return None
    return resolve_team_id_from_text(match.group(1).strip(), alias_to_team_id)


def match_event_priority(event_type: str) -> int:
    return MATCH_EVENT_PRIORITY.get(event_type, 9)


def infer_shot_outcome(event_type_l: str) -> str | None:
    if event_type_l == "own goal":
        return "Own Goal"
    if event_type_l == "attempt blocked":
        return "Blocked"
    if event_type_l in {"miss", "penalty miss", "attempt missed"}:
        return "Missed"
    if event_type_l in {"attempt saved", "penalty saved"}:
        return "Saved"
    if event_type_l == "post":
        return "Hit the Woodwork"
    if event_type_l in {"goal", "penalty goal"}:
        return "Goal"
    return None


def infer_shot_type(event_type_l: str, text_lc: str) -> str:
    if event_type_l == "own goal" or "own goal" in text_lc:
        return "Own Goal"
    if event_type_l in {"penalty goal", "penalty saved", "penalty miss"}:
        return "Penalty"

    set_piece_phrases = (
        "following a corner",
        "following a set piece situation",
        "direct free kick",
        "from a free kick",
    )
    if any(phrase in text_lc for phrase in set_piece_phrases):
        return "Set Piece"

    return "Open Play"


def infer_body_part(text_lc: str) -> str | None:
    if "right footed shot" in text_lc or "right-footed" in text_lc or "right foot" in text_lc:
        return "Right Foot"
    if "left footed shot" in text_lc or "left-footed" in text_lc or "left foot" in text_lc:
        return "Left Foot"
    if "header" in text_lc:
        return "Header"
    if "volley" in text_lc:
        return "Volley"
    return None


def infer_distance(text_lc: str) -> str | None:
    if (
        "outside the box" in text_lc
        or "long range" in text_lc
        or "from distance" in text_lc
        or "well outside the box" in text_lc
    ):
        return "Outside Box"

    inside_phrases = (
        "six yard box",
        "very close range",
        "close range",
        "from close range",
        "in the box",
        "the box",
        "centre of the box",
        "from the centre of the box",
        "from the left side of the box",
        "from the right side of the box",
        "from the six yard box",
        "inside the box",
        "of the box",
        "penalty",
    )
    if any(phrase in text_lc for phrase in inside_phrases):
        return "Inside Box"

    return None


def age_at_date(date_of_birth, reference_dt) -> int | None:
    dob = pd.to_datetime(date_of_birth, errors="coerce")
    ref = pd.to_datetime(reference_dt, utc=True, errors="coerce")
    if pd.isna(dob) or pd.isna(ref):
        return None
    return int(ref.year - dob.year - ((ref.month, ref.day) < (dob.month, dob.day)))

def extract_squad_players(payload) -> list[dict]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []

    for key in ("players", "squad", "content"):
        value = payload.get(key)
        if isinstance(value, list):
            return value

    return []


def extract_birth_date_label(player: dict) -> str | None:
    birth = player.get("birth") or {}
    if isinstance(birth, dict):
        date_block = birth.get("date") or {}
        if isinstance(date_block, dict):
            label = date_block.get("label")
            if label:
                return str(label).strip()

    for key in ("birth_date", "birthDate"):
        value = player.get(key)
        if value:
            return str(value).strip()

    return None

def print_frame_summary(name: str, df: pd.DataFrame) -> None:
    print(f"{name}: {len(df)} rows x {len(df.columns)} cols")

def map_pandas_to_sqlalchemy(col_name: str, series: pd.Series):

    if pd.api.types.is_integer_dtype(series): return Integer
    if pd.api.types.is_float_dtype(series): return Float
    if pd.api.types.is_bool_dtype(series): return Boolean

    name = col_name.lower()
    if name.endswith("_date") or "date_of_birth" in name: return Date
    if "datetime" in name or name.endswith("_at") or "kickoff" in name: return DateTime

    return String