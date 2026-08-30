import pandas as pd
try:
    from tqdm.auto import tqdm
except ImportError:
    def tqdm(iterable, **kwargs):
        return iterable

from pipelines.utils import *
from pipelines.extract.fpl import fetch_fpl_fixtures
from pipelines.extract.pulse import fetch_pulse_fixtures, fetch_pulse_textstream

_KICKOFF_SANITY_TOLERANCE = pd.Timedelta(days=2)

def _fpl_to_pulse_team_map(dim_teams: pd.DataFrame) -> dict[int, int]:
    """fpl_team_id -> Pulse team_id, from dim_teams (the bootstrap-static pulse_id field is reliable)."""
    if dim_teams is None or dim_teams.empty:
        return {}
    if "fpl_team_id" not in dim_teams.columns or "team_id" not in dim_teams.columns:
        return {}

    lookup = dim_teams[["fpl_team_id", "team_id"]].dropna()
    return {int(row.fpl_team_id): int(row.team_id) for row in lookup.itertuples(index=False)}

def _pulse_id_field_is_trustworthy(fpl_fixtures: list[dict]) -> bool:
    """False if any fixture reports pulse_id <= 0, or if two fixtures share a non-null pulse_id."""
    seen: set[int] = set()
    saw_any = False
    for fx in fpl_fixtures:
        pulse_id = fx.get("pulse_id")
        if pulse_id is None:
            continue
        try:
            pulse_id = int(pulse_id)
        except (TypeError, ValueError):
            continue
        if pulse_id <= 0:
            return False
        saw_any = True
        if pulse_id in seen:
            return False
        seen.add(pulse_id)
    return saw_any

def build_fpl_to_pulse_fixture_map(
    fpl_fixtures: list[dict],
    pulse_fixtures: list[dict],
    dim_teams: pd.DataFrame,
) -> dict[int, int]:
    """Returns {fpl_fixture_id: pulse_fixture_id}; tries pulse_id first, falls back to team-pair join."""
    if not fpl_fixtures or not pulse_fixtures:
        return {}

    if _pulse_id_field_is_trustworthy(fpl_fixtures):
        mapping = {}
        for fx in fpl_fixtures:
            fpl_id, pulse_id = fx.get("id"), fx.get("pulse_id")
            if fpl_id is None or pulse_id is None:
                continue
            try:
                mapping[int(fpl_id)] = int(pulse_id)
            except (TypeError, ValueError):
                continue
        if mapping:
            return mapping

    fpl_team_to_pulse_team = _fpl_to_pulse_team_map(dim_teams)
    if not fpl_team_to_pulse_team:
        print(
            "⚠️ Warning: build_fpl_to_pulse_fixture_map: FPL fixtures.pulse_id is "
            "unusable and dim_teams has no fpl_team_id -> team_id mapping to fall "
            "back on. Pass a populated dim_teams to enable the composite-key join."
        )
        return {}

    # Pulse (home_team_id, away_team_id) -> [(pulse_fixture_id, kickoff_millis), ...]
    pulse_by_team_pair: dict[tuple[int, int], list[tuple[int, int | None]]] = {}
    for fx in pulse_fixtures:
        pulse_fixture_id = fx.get("id")
        teams_lst = fx.get("teams", []) or []
        if pulse_fixture_id is None or len(teams_lst) < 2:
            continue
        home_id = (teams_lst[0].get("team", {}) or {}).get("id")
        away_id = (teams_lst[1].get("team", {}) or {}).get("id")
        if home_id is None or away_id is None:
            continue
        key = (int(home_id), int(away_id))
        pulse_by_team_pair.setdefault(key, []).append(
            (int(pulse_fixture_id), fx.get("kickoff", {}).get("millis"))
        )

    mapping: dict[int, int] = {}
    ambiguous: list[int] = []
    kickoff_drift: list[int] = []

    for fx in fpl_fixtures:
        fpl_fixture_id = fx.get("id")
        team_h, team_a = fx.get("team_h"), fx.get("team_a")
        if fpl_fixture_id is None or team_h is None or team_a is None:
            continue

        pulse_home = fpl_team_to_pulse_team.get(int(team_h))
        pulse_away = fpl_team_to_pulse_team.get(int(team_a))
        if pulse_home is None or pulse_away is None:
            continue

        candidates = pulse_by_team_pair.get((pulse_home, pulse_away), [])
        if not candidates:
            continue
        if len(candidates) > 1:
            # More than one candidate for a team pair that should meet once
            # per venue per season is a data anomaly — skip rather than guess.
            ambiguous.append(int(fpl_fixture_id))
            continue

        pulse_fixture_id, pulse_kickoff_millis = candidates[0]

        fpl_kickoff = fx.get("kickoff_time")
        if fpl_kickoff and pulse_kickoff_millis is not None:
            try:
                fpl_dt = pd.to_datetime(fpl_kickoff, utc=True)
                pulse_dt = pd.to_datetime(pulse_kickoff_millis, unit="ms", utc=True)
                if abs(fpl_dt - pulse_dt) > _KICKOFF_SANITY_TOLERANCE:
                    kickoff_drift.append(int(fpl_fixture_id))
            except (TypeError, ValueError):
                pass

        mapping[int(fpl_fixture_id)] = pulse_fixture_id

    if ambiguous:
        print(
            f"⚠️ Warning: build_fpl_to_pulse_fixture_map: {len(ambiguous)} FPL fixture(s) "
            f"matched more than one Pulse fixture for the same team pair and were skipped "
            f"(fpl_fixture_id sample: {ambiguous[:5]})."
        )
    if kickoff_drift:
        print(
            f"⚠️ Warning: build_fpl_to_pulse_fixture_map: {len(kickoff_drift)} fixture(s) "
            f"matched by team pair but have kickoff times more than "
            f"{_KICKOFF_SANITY_TOLERANCE} apart between the FPL and Pulse feeds — kept, "
            f"but worth a manual check (fpl_fixture_id sample: {kickoff_drift[:5]})."
        )

    return mapping

def build_dim_fixtures(season_id: int, dim_teams: pd.DataFrame | None = None) -> pd.DataFrame:
    """Build the season's fixture dimension, including the fpl_fixture_id bridge column.

    dim_teams is required to resolve the FPL<->Pulse crosswalk when
    pulse_id can't be trusted (see build_fpl_to_pulse_fixture_map);
    optional only so existing structural-only callers keep working.
    """

    cols = [
        "fixture_id",
        "fpl_fixture_id",
        "season_id",
        "gameweek",
        "kickoff_datetime",
        "stadium",
        "attendance",
        "home_team_id",
        "away_team_id",
        "fixture_status",
    ]

    payload = fetch_pulse_fixtures(season_id)
    fixtures_list = payload.get("content", []) if isinstance(payload, dict) else []

    if not fixtures_list:
        print(f"⚠️ Warning: No fixtures returned for season_id {season_id}.")
        return pd.DataFrame(columns=cols)

    try:
        fpl_fixtures = fetch_fpl_fixtures()
    except Exception:
        fpl_fixtures = []

    if isinstance(fpl_fixtures, dict):
        fpl_fixtures = fpl_fixtures.get("content", []) or []
    if not isinstance(fpl_fixtures, list):
        fpl_fixtures = []

    fpl_to_pulse_fixture = build_fpl_to_pulse_fixture_map(
        fpl_fixtures, fixtures_list, dim_teams if dim_teams is not None else pd.DataFrame()
    )
    pulse_to_fpl_fixture: dict[int, int] = {
        pulse_id: fpl_id for fpl_id, pulse_id in fpl_to_pulse_fixture.items()
    }

    if fpl_fixtures and not fpl_to_pulse_fixture:
        print(
            "⚠️ Warning: build_dim_fixtures: could not resolve any FPL<->Pulse fixture "
            "mapping (pulse_id looked untrustworthy and the composite-key fallback also "
            "failed — pass dim_teams if you haven't). fpl_fixture_id will be null for "
            "every row, and every fact table downstream of it will come back empty."
        )

    rows = []
    for fixture in fixtures_list:
        fixture_id = fixture.get("id")
        if fixture_id is None:
            continue

        gw_info = fixture.get("gameweek", {}) or {}
        teams_lst = fixture.get("teams", []) or []

        home_team = teams_lst[0].get("team", {}) if len(teams_lst) > 0 else {}
        away_team = teams_lst[1].get("team", {}) if len(teams_lst) > 1 else {}

        home_id = home_team.get("id")
        away_id = away_team.get("id")

        rows.append(
            {
                "fixture_id": int(fixture_id),
                "fpl_fixture_id": pulse_to_fpl_fixture.get(int(fixture_id)),
                "season_id": int(season_id),
                "gameweek": int(gw_info.get("gameweek")) if gw_info.get("gameweek") is not None else None,
                "kickoff_millis": fixture.get("kickoff", {}).get("millis"),
                "stadium": fixture.get("ground", {}).get("name"),
                "attendance": fixture.get("attendance"),
                "home_team_id": int(home_id) if home_id is not None else None,
                "away_team_id": int(away_id) if away_id is not None else None,
                "fixture_status": fixture.get("status"),
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=cols)

    df["kickoff_datetime"] = pd.to_datetime(df["kickoff_millis"], unit="ms", utc=True).dt.strftime("%Y-%m-%d %H:%M:%S")
    df["attendance"] = pd.to_numeric(df["attendance"], errors="coerce").astype("Int64")
    df["fpl_fixture_id"] = pd.to_numeric(df["fpl_fixture_id"], errors="coerce").astype("Int64")

    df = df.dropna(subset=["fixture_id"]).drop_duplicates(subset=["fixture_id"]).copy()
    df["fixture_id"] = df["fixture_id"].astype(int)
    df = df.sort_values(by="fixture_id").reset_index(drop=True)

    print(f"Final dim_fixtures built. Resolved {len(df)} structural fixtures.")
    return df[cols]

def classify_match_event_player_roles(event_type: str, player1_id, player2_id) -> dict:
    """Maps the positional (player1_id, player2_id) pair to fact_match_events' explicit
    role columns based on event_type (goal: scorer/assister; own goal: own_goal_player_id;
    cards: carded_player_id; substitution: player_on_id/player_off_id).
    """
    roles = {
        "scorer_player_id": None,
        "assist_player_id": None,
        "own_goal_player_id": None,
        "carded_player_id": None,
        "player_on_id": None,
        "player_off_id": None,
    }
    if event_type == "goal":
        roles["scorer_player_id"] = player1_id
        roles["assist_player_id"] = player2_id
    elif event_type == "penalty goal":
        roles["scorer_player_id"] = player1_id
    elif event_type == "own goal":
        roles["own_goal_player_id"] = player1_id
    elif event_type in ("yellow", "red"):
        roles["carded_player_id"] = player1_id
    elif event_type == "substitution":
        roles["player_on_id"] = player1_id
        roles["player_off_id"] = player2_id
    return roles

def build_fact_match_events(
    dim_fixtures: pd.DataFrame,
    dim_teams: pd.DataFrame,
    dim_players: pd.DataFrame,
) -> pd.DataFrame:
    output_cols = [
        "match_event_id",
        "fixture_id",
        "season_id",
        "team_id",
        "event_type",
        "scorer_player_id",
        "assist_player_id",
        "own_goal_player_id",
        "carded_player_id",
        "player_on_id",
        "player_off_id",
        "minute",
        "minute_display",
        "is_stoppage_time",
    ]

    if dim_fixtures.empty or dim_teams.empty or dim_players.empty:
        return pd.DataFrame(columns=output_cols)

    fixtures = dim_fixtures[["fixture_id", "season_id", "home_team_id", "away_team_id"]].copy()
    fixtures["fixture_id"] = pd.to_numeric(fixtures["fixture_id"], errors="coerce").astype("Int64")
    fixtures["season_id"] = pd.to_numeric(fixtures["season_id"], errors="coerce").astype("Int64")
    fixtures["home_team_id"] = pd.to_numeric(fixtures["home_team_id"], errors="coerce").astype("Int64")
    fixtures["away_team_id"] = pd.to_numeric(fixtures["away_team_id"], errors="coerce").astype("Int64")
    fixtures = (
        fixtures.dropna(subset=["fixture_id", "season_id", "home_team_id", "away_team_id"])
        .drop_duplicates(subset=["fixture_id"])
        .reset_index(drop=True)
    )
    if fixtures.empty:
        return pd.DataFrame(columns=output_cols)

    teams = dim_teams[["team_id", "team_name", "short_name"]].copy()
    teams["team_id"] = pd.to_numeric(teams["team_id"], errors="coerce").astype("Int64")
    teams = teams.dropna(subset=["team_id"]).drop_duplicates(subset=["team_id"]).reset_index(drop=True)

    known_player_ids = set(
        pd.to_numeric(dim_players["pulse_player_id"], errors="coerce")
        .dropna()
        .astype(int)
        .tolist()
    )

    team_by_id = {
        int(row.team_id): {"team_name": row.team_name, "short_name": row.short_name}
        for row in teams.itertuples(index=False)
    }

    rows: list[dict] = []

    for fixture in tqdm(
        fixtures.itertuples(index=False),
        total=len(fixtures),
        desc="Building fact_match_events",
        unit="fixture",
    ):
        fixture_id = int(fixture.fixture_id)
        season_id = int(fixture.season_id)
        home_team_id = int(fixture.home_team_id)
        away_team_id = int(fixture.away_team_id)
        fixture_team_ids = {home_team_id, away_team_id}

        fixture_alias_to_team_id: dict[str, int] = {}
        for team_id in (home_team_id, away_team_id):
            team_rec = team_by_id.get(team_id, {})
            for alias in (team_rec.get("team_name"), team_rec.get("short_name")):
                alias_norm = normalize_text(alias)
                if alias_norm:
                    fixture_alias_to_team_id[alias_norm] = team_id

        try:
            payload = fetch_pulse_textstream(fixture_id)
        except Exception:
            continue

        events = extract_textstream_events(payload)
        if not events:
            continue

        for ev in events:
            raw_type = str(ev.get("type") or "").strip().lower()
            if not raw_type:
                continue

            text = str(ev.get("text") or "").strip()
            minute_display = format_minute(ev.get("time"))

            raw_player_ids = ev.get("playerIds") or []
            if not isinstance(raw_player_ids, list):
                raw_player_ids = [raw_player_ids]

            player_ids = [
                pid
                for pid in (clean_player_id(pid, known_player_ids) for pid in raw_player_ids)
                if pid is not None
            ]

            team_id = infer_event_team_id(raw_type, text, fixture_alias_to_team_id)
            if team_id is None or team_id not in fixture_team_ids:
                continue

            event_type = None
            __player1_id = None
            __player2_id = None

            if "substitution" in raw_type:
                event_type = "substitution"
                __player1_id = player_ids[0] if len(player_ids) >= 1 else None
                __player2_id = player_ids[1] if len(player_ids) >= 2 else None
            elif "yellow" in raw_type:
                event_type = "yellow"
                __player1_id = player_ids[0] if len(player_ids) >= 1 else None
            elif "red" in raw_type:
                event_type = "red"
                __player1_id = player_ids[0] if len(player_ids) >= 1 else None
            elif "goal" in raw_type:
                if "own goal" in raw_type or "own goal" in normalize_text(text):
                    event_type = "own goal"
                    # team_id above is the conceding team (from the commentary text); an own
                    # goal counts for the *other* team on the scoreboard, so re-point it there.
                    other_teams = fixture_team_ids - {team_id}
                    if len(other_teams) == 1:
                        team_id = next(iter(other_teams))
                elif "penalty" in raw_type or "penalty" in normalize_text(text):
                    event_type = "penalty goal"
                else:
                    event_type = "goal"
                __player1_id = player_ids[0] if len(player_ids) >= 1 else None
                __player2_id = player_ids[1] if len(player_ids) >= 2 else None
            else:
                continue

            if __player1_id is None:
                continue

            minute, is_stoppage_time = parse_minute_components(minute_display)

            rows.append(
                {
                    "fixture_id": fixture_id,
                    "season_id": season_id,
                    "team_id": team_id,
                    "event_type": event_type,
                    **classify_match_event_player_roles(event_type, __player1_id, __player2_id),
                    "minute": minute,
                    "minute_display": minute_display,
                    "is_stoppage_time": is_stoppage_time,
                    "__player1_id": __player1_id,
                    "__player2_id": __player2_id,
                    "__minute_sort": minute_sort_key(minute_display),
                    "__event_priority": match_event_priority(event_type),
                }
            )

    if not rows:
        return pd.DataFrame(columns=output_cols)

    out = pd.DataFrame(rows)
    # Dedup/sort on the original positional pair, not the derived role columns —
    # equivalent, since roles are a deterministic function of event_type + these two.
    dedup_cols = [
        "fixture_id", "season_id", "team_id", "event_type",
        "__player1_id", "__player2_id", "minute_display",
    ]
    out = (
        out.drop_duplicates(subset=dedup_cols, keep="first")
        .sort_values(
            by=["fixture_id", "__minute_sort", "__event_priority", "team_id", "__player1_id", "__player2_id"],
            kind="stable",
        )
        .reset_index(drop=True)
    )

    out = out.drop(columns=[c for c in out.columns if c.startswith("__")], errors="ignore")
    out["fixture_id"] = pd.to_numeric(out["fixture_id"], errors="coerce").astype("Int64")
    out["season_id"] = pd.to_numeric(out["season_id"], errors="coerce").astype("Int64")
    out["team_id"] = pd.to_numeric(out["team_id"], errors="coerce").astype("Int64")
    for role_col in ("scorer_player_id", "assist_player_id", "own_goal_player_id",
                      "carded_player_id", "player_on_id", "player_off_id"):
        out[role_col] = pd.to_numeric(out[role_col], errors="coerce").astype("Int64")
    out["event_type"] = out["event_type"].astype("string")
    out["minute"] = pd.to_numeric(out["minute"], errors="coerce").astype("Int64")
    out["minute_display"] = out["minute_display"].astype("string")
    out["is_stoppage_time"] = out["is_stoppage_time"].astype("boolean")

    # {fixture_id}_{local_index}, generated after the deterministic sort above — stable
    # across re-runs; fixture_id alone makes this fully unique.
    out["match_event_id"] = out["fixture_id"].astype(str) + "_" + out.groupby("fixture_id").cumcount().astype(str)
    out["match_event_id"] = out["match_event_id"].astype("string")

    return out[output_cols].reset_index(drop=True)

def build_fact_shot_events(
    dim_fixtures: pd.DataFrame,
    dim_teams: pd.DataFrame,
    dim_players: pd.DataFrame,
) -> pd.DataFrame:
    output_cols = [
        "shot_event_id",
        "fixture_id",
        "season_id",
        "team_id",
        "player1_id",
        "player2_id",
        "minute",
        "minute_display",
        "is_stoppage_time",
        "shot_type",
        "body_part",
        "distance",
        "outcome",
    ]

    if dim_fixtures.empty or dim_teams.empty or dim_players.empty:
        return pd.DataFrame(columns=output_cols)

    fixtures = dim_fixtures[["fixture_id", "season_id", "home_team_id", "away_team_id"]].copy()
    fixtures["fixture_id"] = pd.to_numeric(fixtures["fixture_id"], errors="coerce").astype("Int64")
    fixtures["season_id"] = pd.to_numeric(fixtures["season_id"], errors="coerce").astype("Int64")
    fixtures["home_team_id"] = pd.to_numeric(fixtures["home_team_id"], errors="coerce").astype("Int64")
    fixtures["away_team_id"] = pd.to_numeric(fixtures["away_team_id"], errors="coerce").astype("Int64")
    fixtures = (
        fixtures.dropna(subset=["fixture_id", "season_id", "home_team_id", "away_team_id"])
        .drop_duplicates(subset=["fixture_id"])
        .reset_index(drop=True)
    )
    if fixtures.empty:
        return pd.DataFrame(columns=output_cols)

    known_player_ids = set(
        pd.to_numeric(dim_players["pulse_player_id"], errors="coerce")
        .dropna()
        .astype(int)
        .tolist()
    )

    rows: list[dict] = []

    for fixture in tqdm(
        fixtures.itertuples(index=False),
        total=len(fixtures),
        desc="Building fact_shot_events",
        unit="fixture",
    ):
        fixture_id = int(fixture.fixture_id)
        season_id = int(fixture.season_id)
        home_team_id = int(fixture.home_team_id)
        away_team_id = int(fixture.away_team_id)
        fixture_team_ids = {home_team_id, away_team_id}

        alias_to_team_id = build_fixture_team_alias_map(dim_teams, home_team_id, away_team_id)

        try:
            payload = fetch_pulse_textstream(fixture_id)
        except Exception:
            continue

        events = extract_textstream_events(payload)
        if not events:
            continue

        for event_idx, ev in enumerate(events):
            event_type_l = str(ev.get("type") or "").strip().lower()
            if event_type_l not in SHOT_EVENT_TYPES:
                continue

            text = str(ev.get("text") or "")
            text_lc = text.lower()
            minute_display = format_minute(ev.get("time"))

            raw_player_ids = ev.get("playerIds") or []
            if not isinstance(raw_player_ids, list):
                raw_player_ids = [raw_player_ids]

            player_ids = [
                pid
                for pid in (clean_player_id(pid, known_player_ids) for pid in raw_player_ids)
                if pid is not None
            ]
            if not player_ids:
                continue

            team_id = infer_event_team_id(event_type_l, text, alias_to_team_id)
            if team_id is None or team_id not in fixture_team_ids:
                continue

            # Deliberately NOT flipped to the "beneficiary" team like fact_match_events —
            # this table's team_id means "team that took the shot" (an own goal is the
            # conceding player's own action, not counted as the other team's shot attempt).

            minute, is_stoppage_time = parse_minute_components(minute_display)

            rows.append(
                {
                    "fixture_id": fixture_id,
                    "season_id": season_id,
                    "team_id": team_id,
                    "player1_id": player_ids[0],
                    "player2_id": player_ids[1] if len(player_ids) > 1 else None,
                    "minute": minute,
                    "minute_display": minute_display,
                    "is_stoppage_time": is_stoppage_time,
                    "shot_type": infer_shot_type(event_type_l, text_lc),
                    "body_part": infer_body_part(text_lc),
                    "distance": infer_distance(text_lc),
                    "outcome": infer_shot_outcome(event_type_l),
                    "__minute_sort": minute_sort_key(minute_display),
                    "__event_idx": event_idx,
                }
            )

    if not rows:
        return pd.DataFrame(columns=output_cols)

    out = pd.DataFrame(rows)
    dedup_cols = [
        "fixture_id", "season_id", "team_id", "player1_id", "player2_id",
        "minute_display", "shot_type", "body_part", "distance", "outcome",
    ]
    out = (
        out.drop_duplicates(subset=dedup_cols, keep="first")
        .sort_values(
            by=["fixture_id", "__minute_sort", "__event_idx", "team_id", "player1_id", "player2_id"],
            kind="stable",
        )
        .reset_index(drop=True)
    )

    out = out.drop(columns=[c for c in out.columns if c.startswith("__")], errors="ignore")
    out["fixture_id"] = pd.to_numeric(out["fixture_id"], errors="coerce").astype("Int64")
    out["season_id"] = pd.to_numeric(out["season_id"], errors="coerce").astype("Int64")
    out["team_id"] = pd.to_numeric(out["team_id"], errors="coerce").astype("Int64")
    out["player1_id"] = pd.to_numeric(out["player1_id"], errors="coerce").astype("Int64")
    out["player2_id"] = pd.to_numeric(out["player2_id"], errors="coerce").astype("Int64")
    out["minute"] = pd.to_numeric(out["minute"], errors="coerce").astype("Int64")
    out["minute_display"] = out["minute_display"].astype("string")
    out["is_stoppage_time"] = out["is_stoppage_time"].astype("boolean")
    out["shot_type"] = out["shot_type"].astype("string")
    out["body_part"] = out["body_part"].astype("string")
    out["distance"] = out["distance"].astype("string")
    out["outcome"] = out["outcome"].astype("string")

    # Same fixture_id-scoped id scheme as build_fact_match_events — see that comment.
    out["shot_event_id"] = out["fixture_id"].astype(str) + "_" + out.groupby("fixture_id").cumcount().astype(str)
    out["shot_event_id"] = out["shot_event_id"].astype("string")

    return out[output_cols].reset_index(drop=True)

