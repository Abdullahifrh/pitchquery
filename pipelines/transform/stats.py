import pandas as pd
try:
    from tqdm.auto import tqdm
except ImportError:
    def tqdm(iterable, **kwargs):
        return iterable

from pipelines.utils import *
from pipelines.extract.fpl import fetch_fpl_live_stats
from pipelines.extract.pulse import fetch_pulse_match_stats, fetch_pulse_player_season_stats

def _build_fpl_to_pulse_player_map(dim_players: pd.DataFrame) -> dict[int, int]:
    """fpl_player_id -> pulse_player_id, used by both live-stats builders below."""
    fpl_players = dim_players[["pulse_player_id", "fpl_player_id"]].copy()
    fpl_players["pulse_player_id"] = pd.to_numeric(fpl_players["pulse_player_id"], errors="coerce").astype("Int64")
    fpl_players["fpl_player_id"] = pd.to_numeric(fpl_players["fpl_player_id"], errors="coerce").astype("Int64")
    fpl_players = (
        fpl_players.dropna(subset=["pulse_player_id", "fpl_player_id"])
        .drop_duplicates(subset=["fpl_player_id"], keep="first")
        .reset_index(drop=True)
    )
    return {
        int(row.fpl_player_id): int(row.pulse_player_id)
        for row in fpl_players.itertuples(index=False)
    }

def build_fact_match_lineup(
    dim_players: pd.DataFrame,
    bridge_player_seasons: pd.DataFrame,
    dim_fixtures: pd.DataFrame
) -> pd.DataFrame:
    """
    Build fact_match_lineup at grain:
        one row per player per fixture

    Output schema:
        player_id, fixture_id, season_id, team_id, minutes_played, starter_flag
    """
    output_cols = [
        "player_id",
        "fixture_id",
        "season_id",
        "team_id",
        "minutes_played",
        "starter_flag",
    ]

    if dim_players.empty or bridge_player_seasons.empty or dim_fixtures.empty:
        return pd.DataFrame(columns=output_cols)

    fixture_lookup = dim_fixtures[
        ["fixture_id", "fpl_fixture_id", "season_id", "kickoff_datetime", "home_team_id", "away_team_id"]
    ].copy()
    fixture_lookup["fixture_id"] = pd.to_numeric(fixture_lookup["fixture_id"], errors="coerce").astype("Int64")
    fixture_lookup["fpl_fixture_id"] = pd.to_numeric(fixture_lookup["fpl_fixture_id"], errors="coerce").astype("Int64")
    fixture_lookup["season_id"] = pd.to_numeric(fixture_lookup["season_id"], errors="coerce").astype("Int64")
    fixture_lookup["home_team_id"] = pd.to_numeric(fixture_lookup["home_team_id"], errors="coerce").astype("Int64")
    fixture_lookup["away_team_id"] = pd.to_numeric(fixture_lookup["away_team_id"], errors="coerce").astype("Int64")
    fixture_lookup["kickoff_datetime"] = pd.to_datetime(fixture_lookup["kickoff_datetime"], utc=True, errors="coerce")
    fixture_lookup = (
        fixture_lookup.dropna(subset=["fixture_id", "season_id", "kickoff_datetime", "home_team_id", "away_team_id"])
        .drop_duplicates(subset=["fixture_id"])
        .reset_index(drop=True)
    )
    if fixture_lookup.empty:
        return pd.DataFrame(columns=output_cols)

    # FPL fixture id -> Pulse fixture id.
    fpl_fixture_to_pulse_fixture = (
        fixture_lookup.dropna(subset=["fpl_fixture_id"])
        .drop_duplicates(subset=["fpl_fixture_id"], keep="first")
        .set_index("fpl_fixture_id")["fixture_id"]
        .to_dict()
    )

    fixture_info = (
        fixture_lookup.set_index("fixture_id")[["season_id", "kickoff_datetime", "home_team_id", "away_team_id"]]
        .to_dict("index")
    )

    fpl_to_pulse_player = _build_fpl_to_pulse_player_map(dim_players)

    bridge = bridge_player_seasons.copy()
    if bridge.empty:
        return pd.DataFrame(columns=output_cols)

    bridge["player_id"] = pd.to_numeric(bridge["player_id"], errors="coerce").astype("Int64")
    bridge["team_id"] = pd.to_numeric(bridge["team_id"], errors="coerce").astype("Int64")
    bridge["first_seen_kickoff_datetime"] = pd.to_datetime(bridge["first_seen_kickoff_datetime"], utc=True, errors="coerce")
    bridge["last_seen_kickoff_datetime"] = pd.to_datetime(bridge["last_seen_kickoff_datetime"], utc=True, errors="coerce")

    bridge_by_player: dict[int, list[dict]] = {}
    for row in bridge.dropna(subset=["player_id", "team_id", "first_seen_kickoff_datetime"]).sort_values(
        by=["player_id", "transfer_sequence", "team_id"],
        kind="stable",
    ).itertuples(index=False):
        last_seen = row.last_seen_kickoff_datetime
        if pd.isna(last_seen):
            last_seen = pd.Timestamp.max.tz_localize("UTC")

        bridge_by_player.setdefault(int(row.player_id), []).append(
            {
                "team_id": int(row.team_id),
                "first_seen": row.first_seen_kickoff_datetime,
                "last_seen": last_seen,
            }
        )

    def team_for_player_fixture(player_id: int, kickoff_dt: pd.Timestamp) -> int | None:
        spells = bridge_by_player.get(player_id, [])
        if not spells:
            return None

        contained = [
            spell for spell in spells
            if pd.notna(spell["first_seen"]) and pd.notna(spell["last_seen"])
            and spell["first_seen"] <= kickoff_dt <= spell["last_seen"]
        ]
        if contained:
            return int(max(contained, key=lambda s: s["first_seen"])["team_id"])

        prior = [
            spell for spell in spells
            if pd.notna(spell["first_seen"]) and spell["first_seen"] <= kickoff_dt
        ]
        if prior:
            return int(max(prior, key=lambda s: s["first_seen"])["team_id"])

        future = [
            spell for spell in spells
            if pd.notna(spell["first_seen"]) and spell["first_seen"] > kickoff_dt
        ]
        if future:
            return int(min(future, key=lambda s: s["first_seen"])["team_id"])

        return int(spells[0]["team_id"])

    if not fpl_to_pulse_player:
        print(
            "  [WARN] build_fact_match_lineup: fpl_to_pulse_player mapping is empty — "
            "dim_players has no rows with both fpl_player_id and pulse_player_id populated. "
            "Every gameweek's live stats will be unmatched and this table will come back empty."
        )
    if not fpl_fixture_to_pulse_fixture:
        print(
            "  [WARN] build_fact_match_lineup: fpl_fixture_to_pulse_fixture mapping is empty — "
            "dim_fixtures has no rows with fpl_fixture_id populated. Every gameweek's live "
            "stats will be unmatched and this table will come back empty."
        )

    rows: list[dict] = []
    seen_keys: set[tuple[int, int]] = set()

    max_gameweek = int(pd.to_numeric(dim_fixtures["gameweek"], errors="coerce").dropna().max())

    fetch_failures: list[tuple[int, str]] = []

    for gw in tqdm(range(1, max_gameweek + 1), desc="Building fact_match_lineup", unit="gw"):
        try:
            payload = fetch_fpl_live_stats(gw)
        except Exception as exc:
            fetch_failures.append((gw, repr(exc)))
            continue

        elements = payload.get("elements", []) if isinstance(payload, dict) else []
        if not elements:
            continue

        for player in elements:
            fpl_player_id = player.get("id")
            if fpl_player_id is None:
                continue

            pulse_player_id = fpl_to_pulse_player.get(int(fpl_player_id))
            if pulse_player_id is None:
                continue

            stats = player.get("stats", {}) or {}
            minutes_played = int(stats.get("minutes") or 0)
            if minutes_played <= 0:
                continue

            starter_flag = 1 if int(stats.get("starts") or 0) > 0 else 0

            explain = player.get("explain") or []
            if not isinstance(explain, list):
                explain = [explain]

            for item in explain:
                fpl_fixture_id = item.get("fixture")
                if fpl_fixture_id is None:
                    continue

                pulse_fixture_id = fpl_fixture_to_pulse_fixture.get(int(fpl_fixture_id))
                if pulse_fixture_id is None:
                    continue

                fixture_row = fixture_info.get(int(pulse_fixture_id))
                if fixture_row is None:
                    continue

                kickoff_dt = fixture_row["kickoff_datetime"]
                team_id = team_for_player_fixture(int(pulse_player_id), kickoff_dt)
                if team_id is None:
                    continue

                home_team_id = int(fixture_row["home_team_id"])
                away_team_id = int(fixture_row["away_team_id"])
                if team_id not in {home_team_id, away_team_id}:
                    continue

                key = (int(pulse_player_id), int(pulse_fixture_id))
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                rows.append(
                    {
                        "player_id": int(pulse_player_id),
                        "fixture_id": int(pulse_fixture_id),
                        "season_id": int(fixture_row["season_id"]),
                        "team_id": int(team_id),
                        "minutes_played": int(minutes_played),
                        "starter_flag": int(starter_flag),
                    }
                )

    if fetch_failures:
        print(
            f"  [WARN] build_fact_match_lineup: fetch_fpl_live_stats failed for "
            f"{len(fetch_failures)}/{max_gameweek} gameweeks. First failure: "
            f"gw={fetch_failures[0][0]} error={fetch_failures[0][1]}"
        )

    if not rows:
        return pd.DataFrame(columns=output_cols)

    out = pd.DataFrame(rows).drop_duplicates(subset=output_cols, keep="first").reset_index(drop=True)
    for col in ["player_id", "fixture_id", "season_id", "team_id", "minutes_played", "starter_flag"]:
        out[col] = pd.to_numeric(out[col], errors="coerce").astype("Int64")

    return out[output_cols].sort_values(by=["fixture_id", "team_id", "player_id"], kind="stable").reset_index(drop=True)

def build_fact_player_fixture_stats(
    fact_match_lineup: pd.DataFrame,
    dim_players: pd.DataFrame,
    dim_fixtures: pd.DataFrame,
    fact_match_events: pd.DataFrame,
) -> pd.DataFrame:

    output_cols = [
        "player_id",
        "fixture_id",
        "season_id",
        "team_id",
        "minutes_played",
        "starter_flag",
        "non_penalty_goals",
        "penalties_missed",
        "penalties_scored",
        "saves",
        "own_goals",
        "defensive_contribution",
        "xg",
        "npxg",
        "xga",
        "xa",
    ]

    if fact_match_lineup.empty or dim_players.empty or dim_fixtures.empty:
        return pd.DataFrame(columns=output_cols)

    lineup = fact_match_lineup[
        ["player_id", "fixture_id", "season_id", "team_id", "minutes_played", "starter_flag"]
    ].copy()

    lineup["player_id"] = pd.to_numeric(lineup["player_id"], errors="coerce").astype("Int64")
    lineup["fixture_id"] = pd.to_numeric(lineup["fixture_id"], errors="coerce").astype("Int64")
    lineup["season_id"] = pd.to_numeric(lineup["season_id"], errors="coerce").astype("Int64")
    lineup["team_id"] = pd.to_numeric(lineup["team_id"], errors="coerce").astype("Int64")
    lineup["minutes_played"] = pd.to_numeric(lineup["minutes_played"], errors="coerce").astype("Int64")
    lineup["starter_flag"] = pd.to_numeric(lineup["starter_flag"], errors="coerce").astype("Int64")

    fixture_lookup = dim_fixtures[["fixture_id", "fpl_fixture_id"]].copy()
    fixture_lookup["fixture_id"] = pd.to_numeric(fixture_lookup["fixture_id"], errors="coerce").astype("Int64")
    fixture_lookup["fpl_fixture_id"] = pd.to_numeric(fixture_lookup["fpl_fixture_id"], errors="coerce").astype("Int64")
    fixture_lookup = fixture_lookup.dropna(subset=["fixture_id"]).drop_duplicates(subset=["fixture_id"])

    fpl_fixture_to_pulse_fixture = {
        int(row.fpl_fixture_id): int(row.fixture_id)
        for row in fixture_lookup.dropna(subset=["fpl_fixture_id"]).itertuples(index=False)
    }

    fpl_to_pulse_player = _build_fpl_to_pulse_player_map(dim_players)

    max_gameweek = int(pd.to_numeric(dim_fixtures["gameweek"], errors="coerce").dropna().max())

    live_rows: list[dict] = []

    for gw in tqdm(range(1, max_gameweek + 1), desc="Building fact_player_fixture_stats", unit="gw"):
        try:
            payload = fetch_fpl_live_stats(gw)
        except Exception:
            continue

        elements = payload.get("elements", []) if isinstance(payload, dict) else []
        if not elements:
            continue

        for player in elements:
            fpl_player_id = player.get("id")
            if fpl_player_id is None:
                continue

            pulse_player_id = fpl_to_pulse_player.get(int(fpl_player_id))
            if pulse_player_id is None:
                continue

            stats = player.get("stats", {}) or {}
            minutes = int(stats.get("minutes") or 0)
            if minutes <= 0:
                continue

            explain = player.get("explain") or []
            if not isinstance(explain, list):
                explain = [explain]
            if not explain:
                continue

            pulse_fixture_id = None
            for item in explain:
                if not isinstance(item, dict):
                    continue
                fpl_fixture_id = item.get("fixture")
                if fpl_fixture_id is None:
                    continue
                pulse_fixture_id = fpl_fixture_to_pulse_fixture.get(int(fpl_fixture_id))
                if pulse_fixture_id is not None:
                    break

            if pulse_fixture_id is None:
                continue

            live_rows.append(
                {
                    "player_id": int(pulse_player_id),
                    "fixture_id": int(pulse_fixture_id),
                    "non_penalty_goals": int(stats.get("goals_scored") or 0),
                    "penalties_missed": int(stats.get("penalties_missed") or 0),
                    "penalties_scored": 0,
                    "saves": int(stats.get("saves") or 0),
                    "own_goals": int(stats.get("own_goals") or 0),
                    "defensive_contribution": int(stats.get("defensive_contribution") or 0),
                    "xg": float(stats.get("expected_goals") or 0.0),
                    "npxg": float(stats.get("expected_goals") or 0.0) - (0.78 * int(stats.get("penalties_missed") or 0)),
                    "xga": float(stats.get("expected_goals_conceded") or 0.0),
                    "xa": float(stats.get("expected_assists") or 0.0),
                    "__stat_row_flag": 1,
                }
            )

    if not live_rows:
        return pd.DataFrame(columns=output_cols)

    stats_df = pd.DataFrame(live_rows).drop_duplicates(subset=["player_id", "fixture_id"], keep="first")
    stats_df["player_id"] = pd.to_numeric(stats_df["player_id"], errors="coerce").astype("Int64")
    stats_df["fixture_id"] = pd.to_numeric(stats_df["fixture_id"], errors="coerce").astype("Int64")

    out = lineup.merge(stats_df, on=["player_id", "fixture_id"], how="left", validate="1:1")

    penalty_patch = (
        fact_match_events.loc[
            fact_match_events["event_type"].eq("penalty goal"),
            ["fixture_id", "scorer_player_id"],
        ]
        .copy()
    )
    if not penalty_patch.empty:
        penalty_patch["fixture_id"] = pd.to_numeric(penalty_patch["fixture_id"], errors="coerce").astype("Int64")
        penalty_patch["scorer_player_id"] = pd.to_numeric(penalty_patch["scorer_player_id"], errors="coerce").astype("Int64")
        penalty_patch = (
            penalty_patch.dropna(subset=["fixture_id", "scorer_player_id"])
            .groupby(["scorer_player_id", "fixture_id"], as_index=False)
            .size()
            .rename(columns={"size": "penalties_scored_patch"})
        )

        out = out.merge(
            penalty_patch,
            left_on=["player_id", "fixture_id"],
            right_on=["scorer_player_id", "fixture_id"],
            how="left",
            validate="1:1",
        ).drop(columns=["scorer_player_id"], errors="ignore")
    else:
        out["penalties_scored_patch"] = 0

    numeric_int_cols = [
        "non_penalty_goals",
        "penalties_missed",
        "penalties_scored",
        "saves",
        "own_goals",
        "defensive_contribution",
    ]
    numeric_float_cols = ["xg", "npxg", "xga", "xa"]

    out["penalties_scored_patch"] = pd.to_numeric(out["penalties_scored_patch"], errors="coerce").fillna(0).astype("Int64")

    out["penalties_scored"] = (
        pd.to_numeric(out["penalties_scored"], errors="coerce").fillna(0).astype("Int64")
        + out["penalties_scored_patch"]
    )
    out["non_penalty_goals"] = (
        pd.to_numeric(out["non_penalty_goals"], errors="coerce").fillna(0).astype("Int64")
        - out["penalties_scored_patch"]
    ).clip(lower=0).astype("Int64")
    out["npxg"] = (
        pd.to_numeric(out["npxg"], errors="coerce").fillna(0.0)
        - (0.78 * out["penalties_scored_patch"].astype(float))
    ).clip(lower=0.0)

    for col in ["minutes_played", "starter_flag", *numeric_int_cols]:
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0).astype("Int64")

    for col in numeric_float_cols:
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0).round(2)

    out = out.drop(columns=["__stat_row_flag", "penalties_scored_patch"], errors="ignore")

    return out[output_cols].sort_values(by=["fixture_id", "team_id", "player_id"], kind="stable").reset_index(drop=True)

def build_fact_team_match_stats(
    dim_fixtures: pd.DataFrame,
    fact_player_fixture_stats: pd.DataFrame,
) -> pd.DataFrame:

    if dim_fixtures.empty or fact_player_fixture_stats.empty:
        return pd.DataFrame(columns=TEAM_MATCH_OUTPUT_COLS)

    fixture_cols = [
        c
        for c in ["fixture_id", "season_id", "home_team_id", "away_team_id", "fixture_status"]
        if c in dim_fixtures.columns
    ]
    fixtures = dim_fixtures.loc[:, fixture_cols].copy()

    for col in ["fixture_id", "season_id", "home_team_id", "away_team_id"]:
        if col in fixtures.columns:
            fixtures[col] = pd.to_numeric(fixtures[col], errors="coerce").astype("Int64")

    if "fixture_status" in fixtures.columns:
        fixtures = fixtures.loc[
            fixtures["fixture_status"].astype(str).str.upper().isin({"C", "FT", "FINISHED"})
        ].copy()

    fixtures = (
        fixtures.dropna(subset=["fixture_id", "season_id", "home_team_id", "away_team_id"])
        .drop_duplicates(subset=["fixture_id"])
        .reset_index(drop=True)
    )

    if fixtures.empty:
        return pd.DataFrame(columns=TEAM_MATCH_OUTPUT_COLS)

    player_df = fact_player_fixture_stats.reindex(
        columns=["fixture_id", "team_id", *TEAM_MATCH_PLAYER_COLS]
    ).copy()

    for col in ["fixture_id", "team_id", "non_penalty_goals", "penalties_scored", "penalties_missed", "own_goals", "saves", "defensive_contribution"]:
        player_df[col] = pd.to_numeric(player_df[col], errors="coerce").astype("Int64")

    for col in ["xg", "xa", "xga", "npxg"]:
        player_df[col] = pd.to_numeric(player_df[col], errors="coerce")

    player_df = player_df.dropna(subset=["fixture_id", "team_id"]).copy()

    team_rollup = (
        player_df.groupby(["fixture_id", "team_id"], as_index=False, sort=False)
        .agg(
            non_penalty_goals=("non_penalty_goals", "sum"),
            penalties_scored=("penalties_scored", "sum"),
            penalties_missed=("penalties_missed", "sum"),
            own_goals=("own_goals", "sum"),
            saves=("saves", "sum"),
            defensive_contribution=("defensive_contribution", "sum"),
            xg=("xg", "sum"),
            xa=("xa", "sum"),
            xga=("xga", "max"),
            npxg=("npxg", "sum"),
        )
    )

    if not team_rollup.empty:
        for col in ["non_penalty_goals", "penalties_scored", "penalties_missed", "own_goals", "saves", "defensive_contribution"]:
            team_rollup[col] = pd.to_numeric(team_rollup[col], errors="coerce").fillna(0).astype("Int64")

        for col in ["xg", "xa", "xga", "npxg"]:
            team_rollup[col] = pd.to_numeric(team_rollup[col], errors="coerce").fillna(0.0).round(2)

    score_wide = fixtures[["fixture_id", "season_id", "home_team_id", "away_team_id"]].copy()

    home_rollup = (
        team_rollup[["fixture_id", "team_id", "non_penalty_goals", "penalties_scored", "own_goals"]]
        .rename(
            columns={
                "team_id": "home_team_id_rollup",
                "non_penalty_goals": "home_non_penalty_goals",
                "penalties_scored": "home_penalties_scored",
                "own_goals": "home_own_goals",
            }
        )
        if not team_rollup.empty
        else pd.DataFrame(columns=["fixture_id", "home_team_id_rollup", "home_non_penalty_goals", "home_penalties_scored", "home_own_goals"])
    )

    away_rollup = (
        team_rollup[["fixture_id", "team_id", "non_penalty_goals", "penalties_scored", "own_goals"]]
        .rename(
            columns={
                "team_id": "away_team_id_rollup",
                "non_penalty_goals": "away_non_penalty_goals",
                "penalties_scored": "away_penalties_scored",
                "own_goals": "away_own_goals",
            }
        )
        if not team_rollup.empty
        else pd.DataFrame(columns=["fixture_id", "away_team_id_rollup", "away_non_penalty_goals", "away_penalties_scored", "away_own_goals"])
    )

    score_wide = score_wide.merge(
        home_rollup,
        left_on=["fixture_id", "home_team_id"],
        right_on=["fixture_id", "home_team_id_rollup"],
        how="left",
    )
    score_wide = score_wide.merge(
        away_rollup,
        left_on=["fixture_id", "away_team_id"],
        right_on=["fixture_id", "away_team_id_rollup"],
        how="left",
    )
    score_wide = score_wide.drop(columns=["home_team_id_rollup", "away_team_id_rollup"], errors="ignore")

    for col in [
        "home_non_penalty_goals",
        "home_penalties_scored",
        "home_own_goals",
        "away_non_penalty_goals",
        "away_penalties_scored",
        "away_own_goals",
    ]:
        score_wide[col] = pd.to_numeric(score_wide[col], errors="coerce").fillna(0).astype("Int64")

    score_wide["home_goals_scored"] = (
        score_wide["home_non_penalty_goals"]
        + score_wide["home_penalties_scored"]
        + score_wide["away_own_goals"]
    )
    score_wide["away_goals_scored"] = (
        score_wide["away_non_penalty_goals"]
        + score_wide["away_penalties_scored"]
        + score_wide["home_own_goals"]
    )

    home_score = score_wide[["fixture_id", "season_id", "home_team_id", "home_goals_scored", "away_goals_scored"]].copy()
    home_score = home_score.rename(
        columns={
            "home_team_id": "team_id",
            "home_goals_scored": "goals_scored",
            "away_goals_scored": "goals_conceded",
        }
    )
    home_score["is_home"] = True

    away_score = score_wide[["fixture_id", "season_id", "away_team_id", "away_goals_scored", "home_goals_scored"]].copy()
    away_score = away_score.rename(
        columns={
            "away_team_id": "team_id",
            "away_goals_scored": "goals_scored",
            "home_goals_scored": "goals_conceded",
        }
    )
    away_score["is_home"] = False

    score_long = pd.concat([home_score, away_score], ignore_index=True)
    score_long["result"] = "D"
    score_long.loc[score_long["goals_scored"] > score_long["goals_conceded"], "result"] = "W"
    score_long.loc[score_long["goals_scored"] < score_long["goals_conceded"], "result"] = "L"
    score_long["points"] = score_long["result"].map({"W": 3, "D": 1, "L": 0}).astype("Int64")

    base = pd.concat(
        [
            fixtures[["fixture_id", "season_id", "home_team_id"]]
            .rename(columns={"home_team_id": "team_id"})
            .assign(is_home=True),
            fixtures[["fixture_id", "season_id", "away_team_id"]]
            .rename(columns={"away_team_id": "team_id"})
            .assign(is_home=False),
        ],
        ignore_index=True,
    )

    out = base.merge(
        team_rollup,
        on=["fixture_id", "team_id"],
        how="left",
        validate="1:1",
    )

    match_rows = []
    for fixture in tqdm(
        fixtures[["fixture_id", "season_id", "home_team_id", "away_team_id"]].itertuples(index=False),
        total=len(fixtures),
        desc="Building fact_team_match_stats",
        unit="fixture",
    ):
        fixture_id = int(fixture.fixture_id)
        season_id = int(fixture.season_id)

        try:
            payload = fetch_pulse_match_stats(fixture_id)
        except Exception:
            payload = {}

        data = payload.get("data", {}) if isinstance(payload, dict) else {}

        for team_id, is_home in ((int(fixture.home_team_id), True), (int(fixture.away_team_id), False)):
            team_block = data.get(str(team_id), {}) if isinstance(data, dict) else {}
            stat_list = team_block.get("M") or team_block.get("m") or team_block.get("match") or []

            row = {
                "fixture_id": fixture_id,
                "season_id": season_id,
                "team_id": team_id,
                "is_home": is_home,
            }

            for entry in stat_list:
                name = entry.get("name")
                if name not in TEAM_MATCH_ENDPOINT_COLS:
                    continue

                value = entry.get("value")

                if name == "formation_used":
                    if value is None:
                        row[name] = pd.NA
                    else:
                        text = str(value).strip()
                        if text.replace(".", "", 1).isdigit():
                            digits = str(int(float(text)))
                            row[name] = "-".join(digits) if 2 <= len(digits) <= 6 else digits
                        else:
                            row[name] = text or pd.NA
                elif name in TEAM_MATCH_FLOAT_COLS:
                    row[name] = float(value or 0.0)
                else:
                    try:
                        row[name] = int(float(value or 0))
                    except Exception:
                        row[name] = 0

            match_rows.append(row)

    match_df = pd.DataFrame(match_rows)

    if not match_df.empty:
        for col in TEAM_MATCH_ENDPOINT_COLS:
            if col not in match_df.columns:
                match_df[col] = 0.0 if col in TEAM_MATCH_FLOAT_COLS else pd.NA

        match_df["formation_used"] = match_df["formation_used"].astype("string")

        for col in TEAM_MATCH_FLOAT_COLS:
            match_df[col] = pd.to_numeric(match_df[col], errors="coerce").fillna(0.0).round(2)

        for col in [c for c in TEAM_MATCH_ENDPOINT_COLS if c not in TEAM_MATCH_FLOAT_COLS and c != "formation_used"]:
            match_df[col] = pd.to_numeric(match_df[col], errors="coerce").fillna(0).astype("Int64")

        out = out.merge(
            match_df,
            on=["fixture_id", "season_id", "team_id", "is_home"],
            how="left",
            validate="1:1",
        )

    out = out.merge(
        score_long[["fixture_id", "season_id", "team_id", "is_home", "goals_scored", "goals_conceded", "result", "points"]],
        on=["fixture_id", "season_id", "team_id", "is_home"],
        how="left",
        validate="1:1",
    )

    for col in ["goals_scored", "goals_conceded", "points"]:
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0).astype("Int64")

    out["result"] = out["result"].fillna("D")

    for col in ["non_penalty_goals", "penalties_scored", "penalties_missed", "own_goals", "saves", "defensive_contribution"]:
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0).astype("Int64")

    for col in ["xg", "xa", "xga", "npxg"]:
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0).round(2)

    for col in TEAM_MATCH_OUTPUT_COLS:
        if col not in out.columns:
            out[col] = pd.NA

    out = out[TEAM_MATCH_OUTPUT_COLS].sort_values(["fixture_id", "team_id"], kind="stable").reset_index(drop=True)
    return out

def build_fact_player_season_stats(
    dim_players: pd.DataFrame,
    fact_player_fixture_stats: pd.DataFrame,
    season_id: int,
    competition_id: int = 1,
) -> pd.DataFrame:
    # accurate_lauches is Opta's own (misspelled) raw feed key — kept verbatim
    # in PLAYER_SEASON_ENDPOINT_COLS for lookup; corrected in output_cols below.
    output_cols = [
        "player_id", "season_id",
        *("accurate_launches" if c == "accurate_lauches" else c for c in PLAYER_SEASON_ENDPOINT_COLS),
        *PLAYER_FIXTURE_DERIVED_COLS,
    ]

    if dim_players.empty and fact_player_fixture_stats.empty:
        return pd.DataFrame(columns=output_cols)

    player_ids: set[int] = set()

    if "pulse_player_id" in dim_players.columns:
        player_ids.update(
            pd.to_numeric(dim_players["pulse_player_id"], errors="coerce").dropna().astype(int).tolist()
        )

    if not fact_player_fixture_stats.empty and {"player_id", "season_id"}.issubset(fact_player_fixture_stats.columns):
        fixture_source = fact_player_fixture_stats.loc[
            pd.to_numeric(fact_player_fixture_stats["season_id"], errors="coerce").astype("Int64").eq(int(season_id))
        ].copy()
        player_ids.update(
            pd.to_numeric(fixture_source["player_id"], errors="coerce").dropna().astype(int).tolist()
        )
    else:
        fixture_source = pd.DataFrame(columns=["player_id", "season_id", *PLAYER_FIXTURE_DERIVED_COLS])

    if not player_ids:
        return pd.DataFrame(columns=output_cols)

    endpoint_rows: list[dict] = []
    for player_id in tqdm(sorted(player_ids), desc="Building fact_player_season_stats", unit="player"):
        try:
            payload = fetch_pulse_player_season_stats(int(player_id), int(season_id), competition_id)
        except Exception:
            payload = {}

        stats_array = payload.get("stats", []) if isinstance(payload, dict) else []
        if isinstance(stats_array, dict):
            stats_array = stats_array.get("stats", []) or []

        stats_lookup = {}
        if isinstance(stats_array, list):
            for item in stats_array:
                if isinstance(item, dict) and item.get("name") is not None:
                    stats_lookup[item["name"]] = item.get("value")

        row = {"player_id": int(player_id), "season_id": int(season_id)}
        for col in PLAYER_SEASON_ENDPOINT_COLS:
            try:
                row[col] = int(float(stats_lookup.get(col, 0) or 0))
            except Exception:
                row[col] = 0

        endpoint_rows.append(row)

    endpoint_df = pd.DataFrame(endpoint_rows)

    derived_cols = [c for c in PLAYER_FIXTURE_DERIVED_COLS if c in fixture_source.columns]
    if derived_cols:
        derived = fixture_source[["player_id", "season_id", *derived_cols]].copy()
        derived["player_id"] = pd.to_numeric(derived["player_id"], errors="coerce").astype("Int64")
        derived["season_id"] = pd.to_numeric(derived["season_id"], errors="coerce").astype("Int64")
        for col in derived_cols:
            derived[col] = pd.to_numeric(derived[col], errors="coerce")
        derived = derived.groupby(["player_id", "season_id"], as_index=False, sort=False)[derived_cols].sum()
    else:
        derived = pd.DataFrame(columns=["player_id", "season_id", *PLAYER_FIXTURE_DERIVED_COLS])

    out = endpoint_df.merge(derived, on=["player_id", "season_id"], how="left", validate="1:1")

    for col in PLAYER_FIXTURE_DERIVED_COLS:
        if col not in out.columns:
            out[col] = 0.0 if col in {"xg", "npxg", "xa"} else 0

    for col in PLAYER_SEASON_ENDPOINT_COLS:
        if col not in out.columns:
            out[col] = 0

    for col in PLAYER_FIXTURE_DERIVED_COLS:
        if col in {"xg", "npxg", "xa"}:
            out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0).round(2)
        else:
            out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0).astype("Int64")

    for col in PLAYER_SEASON_ENDPOINT_COLS:
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0).astype("Int64")

    out = out.rename(columns={"accurate_lauches": "accurate_launches"})

    return out[output_cols].sort_values(["player_id"], kind="stable").reset_index(drop=True)

def build_fact_team_season_stats(fact_team_match_stats: pd.DataFrame) -> pd.DataFrame:
    if fact_team_match_stats.empty:
        return pd.DataFrame(columns=["team_id", "season_id"])

    df = fact_team_match_stats.copy()

    for col in ["season_id", "team_id", "goals_scored", "goals_conceded", "possession_percentage", "ppda"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "formation_used" in df.columns:
        df["formation_used"] = df["formation_used"].astype("string")
    if "result" in df.columns:
        df["result"] = df["result"].fillna("D")

    exclude = {
        "fixture_id",
        "season_id",
        "team_id",
        "is_home",
        "result",
        "formation_used",
        "possession_percentage",
        "ppda",
        "xg",
        "xga",
        "matches_played",
        "wins",
        "draws",
        "losses",
        "goals_difference",
        "points",
    }
    sum_cols = [c for c in df.columns if c not in exclude]

    agg_spec = {}
    if "formation_used" in df.columns:
        agg_spec["formation_used"] = (
            "formation_used",
            lambda s: s.dropna().mode().iloc[0] if not s.dropna().mode().empty else pd.NA,
        )
    if "possession_percentage" in df.columns:
        agg_spec["possession_percentage"] = ("possession_percentage", "mean")
    if "ppda" in df.columns:
        agg_spec["ppda"] = ("ppda", "mean")
    for col in sum_cols:
        agg_spec[col] = (col, "sum")

    season_df = df.groupby(["season_id", "team_id"], as_index=False, sort=False).agg(**agg_spec)

    for col in ["possession_percentage", "ppda"]:
        if col in season_df.columns:
            season_df[col] = pd.to_numeric(season_df[col], errors="coerce").fillna(0.0).round(2)

    for col in sum_cols:
        if col in season_df.columns:
            if col in {"xa", "npxg"}:
                season_df[col] = pd.to_numeric(season_df[col], errors="coerce").fillna(0.0).round(2)
            else:
                season_df[col] = pd.to_numeric(season_df[col], errors="coerce").fillna(0).astype("Int64")

    ordered = ["team_id", "season_id"]
    if "formation_used" in season_df.columns:
        ordered.append("formation_used")
    if "possession_percentage" in season_df.columns:
        ordered.append("possession_percentage")
    if "ppda" in season_df.columns:
        ordered.append("ppda")
    ordered.extend([c for c in sum_cols if c in season_df.columns])

    return season_df[ordered].sort_values(["season_id", "team_id"], kind="stable").reset_index(drop=True)

def build_fact_premier_league_table(fact_team_match_stats: pd.DataFrame) -> pd.DataFrame:
    output_cols = [
        "team_id",
        "season_id",
        "matches_played",
        "wins",
        "draws",
        "losses",
        "goals_for",
        "goals_against",
        "goals_difference",
        "points",
        "home_matches_played",
        "home_wins",
        "home_draws",
        "home_losses",
        "home_goals_for",
        "home_goals_against",
        "home_goals_difference",
        "home_points",
        "away_matches_played",
        "away_wins",
        "away_draws",
        "away_losses",
        "away_goals_for",
        "away_goals_against",
        "away_goals_difference",
        "away_points",
        "xg",
        "xga",
        "xgd",
    ]

    if fact_team_match_stats.empty:
        return pd.DataFrame(columns=output_cols)

    df = fact_team_match_stats.copy()

    for col in ["season_id", "team_id", "goals_scored", "goals_conceded", "points"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "result" in df.columns:
        df["result"] = df["result"].fillna("D")

    xg_source = "xg" if "xg" in df.columns else None
    xga_source = "xga" if "xga" in df.columns else None
    overall = df.groupby(["season_id", "team_id"], as_index=False, sort=False).agg(
        matches_played=("fixture_id", "size"),
        wins=("result", lambda s: int((s == "W").sum())),
        draws=("result", lambda s: int((s == "D").sum())),
        losses=("result", lambda s: int((s == "L").sum())),
        goals_for=("goals_scored", "sum"),
        goals_against=("goals_conceded", "sum"),
        points=("points", "sum"),
    )

    if xg_source:
        overall["xg"] = df.groupby(["season_id", "team_id"], sort=False)[xg_source].sum().values
    else:
        overall["xg"] = 0.0

    if xga_source:
        overall["xga"] = df.groupby(["season_id", "team_id"], sort=False)[xga_source].sum().values
    else:
        overall["xga"] = 0.0

    overall["goals_difference"] = (
        pd.to_numeric(overall["goals_for"], errors="coerce").fillna(0)
        - pd.to_numeric(overall["goals_against"], errors="coerce").fillna(0)
    )
    overall["xgd"] = (
        pd.to_numeric(overall["xg"], errors="coerce").fillna(0.0)
        - pd.to_numeric(overall["xga"], errors="coerce").fillna(0.0)
    ).round(2)

    home_df = df.loc[df["is_home"].eq(True)].copy()
    away_df = df.loc[df["is_home"].eq(False)].copy()

    home = (
        home_df.groupby(["season_id", "team_id"], as_index=False, sort=False).agg(
            home_matches_played=("fixture_id", "size"),
            home_wins=("result", lambda s: int((s == "W").sum())),
            home_draws=("result", lambda s: int((s == "D").sum())),
            home_losses=("result", lambda s: int((s == "L").sum())),
            home_goals_for=("goals_scored", "sum"),
            home_goals_against=("goals_conceded", "sum"),
            home_points=("points", "sum"),
        )
        if not home_df.empty
        else pd.DataFrame(columns=["season_id", "team_id"])
    )
    if not home.empty:
        home["home_goals_difference"] = (
            pd.to_numeric(home["home_goals_for"], errors="coerce").fillna(0)
            - pd.to_numeric(home["home_goals_against"], errors="coerce").fillna(0)
        )

    away = (
        away_df.groupby(["season_id", "team_id"], as_index=False, sort=False).agg(
            away_matches_played=("fixture_id", "size"),
            away_wins=("result", lambda s: int((s == "W").sum())),
            away_draws=("result", lambda s: int((s == "D").sum())),
            away_losses=("result", lambda s: int((s == "L").sum())),
            away_goals_for=("goals_scored", "sum"),
            away_goals_against=("goals_conceded", "sum"),
            away_points=("points", "sum"),
        )
        if not away_df.empty
        else pd.DataFrame(columns=["season_id", "team_id"])
    )
    if not away.empty:
        away["away_goals_difference"] = (
            pd.to_numeric(away["away_goals_for"], errors="coerce").fillna(0)
            - pd.to_numeric(away["away_goals_against"], errors="coerce").fillna(0)
        )

    out = overall.merge(home, on=["season_id", "team_id"], how="left")
    out = out.merge(away, on=["season_id", "team_id"], how="left")

    for col in output_cols:
        if col not in out.columns:
            out[col] = 0 if col not in {"team_id", "season_id"} else pd.NA

    for col in [
        "matches_played",
        "wins",
        "draws",
        "losses",
        "goals_for",
        "goals_against",
        "goals_difference",
        "points",
        "home_matches_played",
        "home_wins",
        "home_draws",
        "home_losses",
        "home_goals_for",
        "home_goals_against",
        "home_goals_difference",
        "home_points",
        "away_matches_played",
        "away_wins",
        "away_draws",
        "away_losses",
        "away_goals_for",
        "away_goals_against",
        "away_goals_difference",
        "away_points",
    ]:
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0).astype("Int64")

    out["xg"] = pd.to_numeric(out["xg"], errors="coerce").fillna(0.0).round(2)
    out["xga"] = pd.to_numeric(out["xga"], errors="coerce").fillna(0.0).round(2)
    out["xgd"] = pd.to_numeric(out["xgd"], errors="coerce").fillna(0.0).round(2)

    return out[output_cols].sort_values(["season_id", "team_id"], kind="stable").reset_index(drop=True)

