import pandas as pd
try:
    from tqdm.auto import tqdm
except ImportError:
    def tqdm(iterable, **kwargs):
        return iterable

from pipelines.utils import *
from pipelines.extract.fpl import fetch_fpl_bootstrap, fetch_fpl_teams
from pipelines.extract.pulse import fetch_pulse_players_list, fetch_pulse_squad
from pipelines.transform.teams import build_dim_teams

def build_pulse_players_list(season_id: int) -> pd.DataFrame:
    first_page = fetch_pulse_players_list(season_id, page=0)
    num_pages = max(int(first_page.get("pageInfo", {}).get("numPages", 1)), 1)

    rows = []
    for page in range(num_pages):
        payload = first_page if page == 0 else fetch_pulse_players_list(season_id, page=page)

        for player in payload.get("content", []):
            pulse_player_id = player.get("id")
            alt_ids = player.get("altIds") or {}
            opta_code = alt_ids.get("opta")

            if pulse_player_id is None or not isinstance(opta_code, str):
                continue

            opta_code = opta_code.strip()
            if opta_code[:1].lower() == "p":
                opta_code = opta_code[1:].strip()

            if not opta_code:
                continue

            position = player.get("info", {}).get("position", None)
            position_info = player.get("info", {}).get("positionInfo", None)
            shirt_number = player.get("info", {}).get("shirtNum", None)
            current_team_id = player.get("currentTeam", {}).get("id", None)

            rows.append(
                {
                    "pulse_player_id": int(pulse_player_id),
                    "opta_code": opta_code,
                    "player_name": player.get("name", {}).get("display"),
                    "country": player.get("nationalTeam", {}).get("country"),
                    "position": position,
                    "position_info": position_info,
                    "shirt_number": int(shirt_number) if shirt_number is not None else None,
                    "current_team_id": int(current_team_id) if current_team_id is not None else None,
                }
            )

    df = pd.DataFrame(
        rows,
        columns=[
            "pulse_player_id",
            "opta_code",
            "player_name",
            "country",
            "position",
            "position_info",
            "shirt_number",
            "current_team_id",
        ],
    )

    if df.empty:
        return pd.DataFrame(
            columns=[
                "pulse_player_id",
                "opta_code",
                "player_name",
                "country",
                "position",
                "position_info",
                "shirt_number",
                "current_team_id",
            ]
        )

    df["pulse_player_id"] = df["pulse_player_id"].astype("Int64")
    df["current_team_id"] = df["current_team_id"].astype("Int64")
    df["opta_code"] = df["opta_code"].astype("string").str.strip()
    df["player_name"] = df["player_name"].astype("string").str.strip()
    df["country"] = df["country"].astype("string").str.strip()
    df["position"] = df["position"].astype("string").str.strip()
    df["position_info"] = df["position_info"].astype("string").str.strip()
    df["shirt_number"] = df["shirt_number"].astype("Int64")

    df = (
        df.dropna(subset=["pulse_player_id", "opta_code"])
          .drop_duplicates(subset=["pulse_player_id"], keep="first")
          .reset_index(drop=True)
    )
    return df

def build_dim_players(season_id: int, dim_teams: pd.DataFrame | None = None) -> pd.DataFrame:

    output_cols = [
        "pulse_player_id",
        "fpl_player_id",
        "opta_code",
        "player_name",
        "country",
        "position",
        "position_info",
        "shirt_number",
        "current_team_id",
        "date_of_birth",
        "player_photo_url",
    ]

    if dim_teams is None:
        dim_teams = build_dim_teams(fetch_fpl_teams())

    pulse_list = build_pulse_players_list(season_id)

    squad_rows = []
    team_ids = (
        dim_teams["team_id"]
        .dropna()
        .astype(int)
        .drop_duplicates()
        .sort_values(kind="stable")
        .tolist()
    )

    for team_id in tqdm(team_ids, desc="Building Pulse squad players", unit="team"):
        try:
            payload = fetch_pulse_squad(team_id, season_id)
        except Exception:
            continue

        players = []
        if isinstance(payload, list):
            players = payload
        elif isinstance(payload, dict):
            for key in ("players", "squad", "content"):
                value = payload.get(key)
                if isinstance(value, list):
                    players = value
                    break

        for player in players:
            pulse_player_id = player.get("id")
            if pulse_player_id is None:
                pulse_player_id = player.get("playerId")
            if pulse_player_id is None:
                continue

            alt_ids = player.get("altIds") or {}
            opta_code = alt_ids.get("opta")
            if isinstance(opta_code, str):
                opta_code = opta_code.strip()
                if opta_code[:1].lower() == "p":
                    opta_code = opta_code[1:].strip()
            else:
                opta_code = None

            info = player.get("info") or {}
            national_team = player.get("nationalTeam") or {}
            birth = player.get("birth") or {}
            birth_date = None
            if isinstance(birth, dict):
                birth_date = (birth.get("date") or {}).get("label")

            squad_rows.append(
                {
                    "pulse_player_id": int(pulse_player_id),
                    "opta_code": opta_code,
                    "player_name": (player.get("name") or {}).get("display"),
                    "country": national_team.get("country"),
                    "position": info.get("position"),
                    "position_info": info.get("positionInfo"),
                    "shirt_number": info.get("shirtNum"),
                    "current_team_id": team_id,
                    "date_of_birth": birth_date,
                }
            )

    pulse_squad = pd.DataFrame(
        squad_rows,
        columns=[
            "pulse_player_id",
            "opta_code",
            "player_name",
            "country",
            "position",
            "position_info",
            "shirt_number",
            "current_team_id",
            "date_of_birth",
        ],
    )

    pulse_frames = []
    if not pulse_list.empty:
        pulse_frames.append(pulse_list.assign(_source_priority=0))
    if not pulse_squad.empty:
        pulse_frames.append(pulse_squad.assign(_source_priority=1))

    if pulse_frames:
        pulse_all = pd.concat(pulse_frames, ignore_index=True)
    else:
        pulse_all = pd.DataFrame(
            columns=[
                "pulse_player_id",
                "opta_code",
                "player_name",
                "country",
                "position",
                "position_info",
                "shirt_number",
                "current_team_id",
                "date_of_birth",
                "_source_priority",
            ]
        )

    if pulse_all.empty:
        final_dim = pd.DataFrame(columns=output_cols)
        return final_dim

    pulse_all["pulse_player_id"] = pd.to_numeric(pulse_all["pulse_player_id"], errors="coerce").astype("Int64")
    pulse_all["shirt_number"] = pd.to_numeric(pulse_all["shirt_number"], errors="coerce").astype("Int64")
    pulse_all["current_team_id"] = pd.to_numeric(pulse_all["current_team_id"], errors="coerce").astype("Int64")
    pulse_all["_source_priority"] = pd.to_numeric(pulse_all["_source_priority"], errors="coerce").astype("Int64")

    for col in ["opta_code", "player_name", "country", "position", "position_info", "date_of_birth"]:
        pulse_all[col] = pulse_all[col].astype("string").str.strip()

    pulse_all = (
        pulse_all.dropna(subset=["pulse_player_id"])
        .sort_values(
            by=["pulse_player_id", "_source_priority"],
            ascending=[True, True],
            kind="stable",
        )
        .groupby("pulse_player_id", as_index=False)
        .first()
        .reset_index(drop=True)
    )

    bootstrap = fetch_fpl_bootstrap()
    fpl_rows = []
    for player in bootstrap.get("elements", []):
        fpl_player_id = player.get("id")
        fpl_code = player.get("code")
        if fpl_player_id is None or fpl_code is None:
            continue

        opta_code = str(fpl_code).strip()
        if opta_code[:1].lower() == "p":
            opta_code = opta_code[1:].strip()
        if not opta_code:
            continue

        fpl_rows.append(
            {
                "fpl_player_id": int(fpl_player_id),
                "opta_code": opta_code,
                "player_photo_url": f"https://resources.premierleague.com/premierleague/photos/players/110x140/p{str(fpl_code).strip().lstrip('p').lstrip('P')}.png",
            }
        )

    df_fpl = pd.DataFrame(
        fpl_rows,
        columns=["fpl_player_id", "opta_code", "player_photo_url"],
    )

    if not df_fpl.empty:
        df_fpl["fpl_player_id"] = pd.to_numeric(df_fpl["fpl_player_id"], errors="coerce").astype("Int64")
        df_fpl["opta_code"] = df_fpl["opta_code"].astype("string").str.strip()
        df_fpl["player_photo_url"] = df_fpl["player_photo_url"].astype("string").str.strip()
        df_fpl = df_fpl.drop_duplicates(subset=["opta_code"], keep="first").copy()

        final_dim = pulse_all.merge(df_fpl, on="opta_code", how="left", validate="m:1")
    else:
        final_dim = pulse_all.copy()
        final_dim["fpl_player_id"] = pd.NA
        final_dim["player_photo_url"] = pd.NA

    final_dim["fpl_player_id"] = pd.to_numeric(final_dim["fpl_player_id"], errors="coerce").astype("Int64")
    final_dim["current_team_id"] = pd.to_numeric(final_dim["current_team_id"], errors="coerce").astype("Int64")
    final_dim["shirt_number"] = pd.to_numeric(final_dim["shirt_number"], errors="coerce").astype("Int64")

    if "player_photo_url" not in final_dim.columns:
        final_dim["player_photo_url"] = pd.NA

    for col in ["opta_code", "player_name", "country", "position", "position_info", "date_of_birth", "player_photo_url"]:
        final_dim[col] = final_dim[col].astype("string").str.strip()

    # RAG-readiness fix: date_of_birth stored as a real date, not free text.
    final_dim["date_of_birth"] = parse_pulse_birth_date(final_dim["date_of_birth"]).dt.date

    final_dim = final_dim[
        [
            "pulse_player_id",
            "fpl_player_id",
            "opta_code",
            "player_name",
            "country",
            "position",
            "position_info",
            "shirt_number",
            "current_team_id",
            "date_of_birth",
            "player_photo_url",
        ]
    ].copy()

    final_dim = (
        final_dim.drop_duplicates(subset=["pulse_player_id"], keep="first")
        .sort_values(by=["player_name", "pulse_player_id"], na_position="last", kind="stable")
        .reset_index(drop=True)
    )

    return final_dim[output_cols]

def build_bridge_player_seasons(
    dim_players: pd.DataFrame,
    fact_match_events: pd.DataFrame,
    fact_shot_events: pd.DataFrame,
    dim_fixtures: pd.DataFrame,
    dim_teams: pd.DataFrame,
) -> pd.DataFrame:
    output_cols = [
        "bridge_player_season_id",
        "player_id",
        "season_id",
        "team_id",
        "transfer_sequence",
        "first_seen_fixture_id",
        "last_seen_fixture_id",
        "first_seen_kickoff_datetime",
        "last_seen_kickoff_datetime",
        "position",
        "position_info",
        "shirt_number",
        "age",
    ]

    if dim_players.empty or dim_fixtures.empty or dim_teams.empty:
        return pd.DataFrame(columns=output_cols)

    season_ids = pd.to_numeric(dim_fixtures["season_id"], errors="coerce").dropna().unique()
    if len(season_ids) != 1:
        raise ValueError("build_bridge_player_seasons expects exactly one season_id in dim_fixtures")
    season_id = int(season_ids[0])

    valid_team_ids = set(
        pd.to_numeric(dim_teams["team_id"], errors="coerce")
        .dropna()
        .astype(int)
        .tolist()
    )

    fixture_lookup = dim_fixtures[["fixture_id", "kickoff_datetime"]].copy()
    fixture_lookup["fixture_id"] = pd.to_numeric(fixture_lookup["fixture_id"], errors="coerce").astype("Int64")
    fixture_lookup["kickoff_datetime"] = pd.to_datetime(fixture_lookup["kickoff_datetime"], utc=True, errors="coerce")
    fixture_lookup = (
        fixture_lookup.dropna(subset=["fixture_id", "kickoff_datetime"])
        .drop_duplicates(subset=["fixture_id"])
        .reset_index(drop=True)
    )
    if fixture_lookup.empty:
        return pd.DataFrame(columns=output_cols)

    season_start_dt = fixture_lookup["kickoff_datetime"].min()
    season_end_dt = fixture_lookup["kickoff_datetime"].max()

    player_lookup = dim_players[
        ["pulse_player_id", "date_of_birth", "position", "position_info", "shirt_number", "current_team_id"]
    ].copy()
    player_lookup["pulse_player_id"] = pd.to_numeric(player_lookup["pulse_player_id"], errors="coerce").astype("Int64")
    player_lookup["current_team_id"] = pd.to_numeric(player_lookup["current_team_id"], errors="coerce").astype("Int64")
    player_lookup["shirt_number"] = pd.to_numeric(player_lookup["shirt_number"], errors="coerce").astype("Int64")
    player_lookup["date_of_birth"] = pd.to_datetime(player_lookup["date_of_birth"], errors="coerce")
    player_lookup = (
        player_lookup.dropna(subset=["pulse_player_id"])
        .drop_duplicates(subset=["pulse_player_id"], keep="first")
        .reset_index(drop=True)
    )
    player_meta = player_lookup.set_index("pulse_player_id", drop=False)

    # Passed explicitly per frame rather than guessed — fact_match_events and fact_shot_events
    # have different player-column shapes; a missing column raises here instead of
    # silently returning an empty frame (which previously dropped rows silently).
    MATCH_EVENT_PLAYER_COLS = (
        "scorer_player_id", "assist_player_id", "own_goal_player_id",
        "carded_player_id", "player_on_id", "player_off_id",
    )
    SHOT_EVENT_PLAYER_COLS = ("player1_id", "player2_id")

    def long_obs(df: pd.DataFrame, player_cols: tuple[str, ...]) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame(columns=["player_id", "fixture_id", "team_id"])
        if not {"fixture_id", "team_id"}.issubset(df.columns):
            return pd.DataFrame(columns=["player_id", "fixture_id", "team_id"])

        missing = [c for c in player_cols if c not in df.columns]
        if missing:
            raise KeyError(
                f"long_obs: expected player column(s) {missing} not found in this frame "
                f"(columns present: {list(df.columns)}) — the frame's shape no longer matches "
                f"what this call site expects; update MATCH_EVENT_PLAYER_COLS/SHOT_EVENT_PLAYER_COLS "
                f"above to match, rather than silently skipping (see the comment above this function)."
            )

        parts = []
        for player_col in player_cols:
            tmp = df[["fixture_id", "team_id", player_col]].copy()
            tmp = tmp.rename(columns={player_col: "player_id"})
            parts.append(tmp)

        out = pd.concat(parts, ignore_index=True)
        for col in ("player_id", "fixture_id", "team_id"):
            out[col] = pd.to_numeric(out[col], errors="coerce").astype("Int64")

        out = out.dropna(subset=["player_id", "fixture_id", "team_id"]).copy()
        out = out[out["team_id"].isin(valid_team_ids)].copy()
        return out.drop_duplicates(subset=["player_id", "fixture_id", "team_id"], keep="first")

    observations = pd.concat(
        [
            long_obs(fact_match_events, MATCH_EVENT_PLAYER_COLS),
            long_obs(fact_shot_events, SHOT_EVENT_PLAYER_COLS),
        ],
        ignore_index=True,
    )

    rows: list[dict] = []
    observed_player_ids: set[int] = set()

    if not observations.empty:
        observations = observations.merge(
            fixture_lookup,
            on="fixture_id",
            how="inner",
            validate="m:1",
        ).dropna(subset=["kickoff_datetime"]).copy()

        observations = observations.sort_values(
            by=["player_id", "kickoff_datetime", "fixture_id", "team_id"],
            kind="stable",
        ).reset_index(drop=True)

        for player_id, group in observations.groupby("player_id", sort=False):
            if player_id not in player_meta.index:
                continue

            observed_player_ids.add(int(player_id))

            meta = player_meta.loc[player_id]
            date_of_birth = meta["date_of_birth"]
            position = meta["position"] if pd.notna(meta["position"]) else pd.NA
            position_info = meta["position_info"] if pd.notna(meta["position_info"]) else pd.NA
            shirt_number = meta["shirt_number"] if pd.notna(meta["shirt_number"]) else pd.NA

            current_team = None
            transfer_sequence = 0
            spell_start_dt = None
            spell_end_dt = None
            spell_start_fixture_id = None
            spell_end_fixture_id = None

            for row in group.itertuples(index=False):
                team_id = int(row.team_id)

                if current_team is None:
                    current_team = team_id
                    transfer_sequence = 1
                    spell_start_dt = row.kickoff_datetime
                    spell_end_dt = row.kickoff_datetime
                    spell_start_fixture_id = int(row.fixture_id)
                    spell_end_fixture_id = int(row.fixture_id)
                    continue

                if team_id != current_team:
                    rows.append(
                        {
                            "player_id": int(player_id),
                            "season_id": season_id,
                            "team_id": int(current_team),
                            "transfer_sequence": int(transfer_sequence),
                            "first_seen_fixture_id": int(spell_start_fixture_id),
                            "last_seen_fixture_id": int(spell_end_fixture_id),
                            "first_seen_kickoff_datetime": spell_start_dt,
                            "last_seen_kickoff_datetime": spell_end_dt,
                            "position": position,
                            "position_info": position_info,
                            "shirt_number": shirt_number,
                            "age": age_at_date(date_of_birth, spell_start_dt),
                        }
                    )
                    current_team = team_id
                    transfer_sequence += 1
                    spell_start_dt = row.kickoff_datetime
                    spell_end_dt = row.kickoff_datetime
                    spell_start_fixture_id = int(row.fixture_id)
                    spell_end_fixture_id = int(row.fixture_id)
                else:
                    spell_end_dt = row.kickoff_datetime
                    spell_end_fixture_id = int(row.fixture_id)

            rows.append(
                {
                    "player_id": int(player_id),
                    "season_id": season_id,
                    "team_id": int(current_team),
                    "transfer_sequence": int(transfer_sequence),
                    "first_seen_fixture_id": int(spell_start_fixture_id),
                    "last_seen_fixture_id": int(spell_end_fixture_id),
                    "first_seen_kickoff_datetime": spell_start_dt,
                    "last_seen_kickoff_datetime": spell_end_dt,
                    "position": position,
                    "position_info": position_info,
                    "shirt_number": shirt_number,
                    "age": age_at_date(date_of_birth, spell_start_dt),
                }
            )

    fallback_players = player_lookup[
        player_lookup["current_team_id"].isin(valid_team_ids)
        & (~player_lookup["pulse_player_id"].isin(observed_player_ids))
    ].copy()

    for row in fallback_players.sort_values(by="pulse_player_id", kind="stable").itertuples(index=False):
        rows.append(
            {
                "player_id": int(row.pulse_player_id),
                "season_id": season_id,
                "team_id": int(row.current_team_id),
                "transfer_sequence": 1,
                "first_seen_fixture_id": pd.NA,
                "last_seen_fixture_id": pd.NA,
                "first_seen_kickoff_datetime": season_start_dt,
                "last_seen_kickoff_datetime": season_end_dt,
                "position": row.position if pd.notna(row.position) else pd.NA,
                "position_info": row.position_info if pd.notna(row.position_info) else pd.NA,
                "shirt_number": row.shirt_number if pd.notna(row.shirt_number) else pd.NA,
                "age": age_at_date(row.date_of_birth, season_start_dt),
            }
        )

    if not rows:
        return pd.DataFrame(columns=output_cols)

    bridge = pd.DataFrame(rows)
    bridge = (
        bridge.drop_duplicates(
            subset=["player_id", "season_id", "team_id", "transfer_sequence"],
            keep="first",
        )
        .sort_values(by=["player_id", "transfer_sequence", "team_id"], kind="stable")
        .reset_index(drop=True)
    )

    bridge.insert(0, "bridge_player_season_id", (
        bridge["player_id"].astype(str) + "_" + bridge["transfer_sequence"].astype(str)
    ))
    bridge["bridge_player_season_id"] = bridge["bridge_player_season_id"].astype("string")
    # Deterministic from (player_id, transfer_sequence) — stable across any re-run or re-sort.

    for col in [
        "player_id",
        "season_id",
        "team_id",
        "transfer_sequence",
        "first_seen_fixture_id",
        "last_seen_fixture_id",
        "shirt_number",
        "age",
    ]:
        bridge[col] = pd.to_numeric(bridge[col], errors="coerce").astype("Int64")

    bridge["first_seen_kickoff_datetime"] = pd.to_datetime(bridge["first_seen_kickoff_datetime"], utc=True, errors="coerce")
    bridge["last_seen_kickoff_datetime"] = pd.to_datetime(bridge["last_seen_kickoff_datetime"], utc=True, errors="coerce")

    return bridge[output_cols].reset_index(drop=True)

