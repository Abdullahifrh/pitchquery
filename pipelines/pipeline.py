import pandas as pd
import requests
import argparse
try:
    from tqdm.auto import tqdm
except ImportError:
    def tqdm(iterable, **kwargs):
        return iterable

from pipelines.utils import *
from pipelines.snapshot import export_csv_snapshot

REQUEST_TIMEOUT = 10
PULSE_ORIGIN = "https://www.premierleague.com"
PULSE_HEADERS = {
    "Origin": PULSE_ORIGIN,
    "Referer": f"{PULSE_ORIGIN}/",
    "User-Agent": "Mozilla/5.0",
}

HTTP_WORKER = requests.Session()
HTTP_WORKER.headers.update(PULSE_HEADERS)
# Maximize connection pool capacity so parallel loops never block
adapter = requests.adapters.HTTPAdapter(pool_connections=100, pool_maxsize=100)
HTTP_WORKER.mount("https://", adapter)
HTTP_WORKER.mount("http://", adapter)

FPL_BOOTSTRAP_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"
FPL_FIXTURES_URL = "https://fantasy.premierleague.com/api/fixtures/"
FPL_LIVE_URL = "https://fantasy.premierleague.com/api/event/{gameweek}/live/"

PULSE_COMP_SEASONS_URL = "https://footballapi.pulselive.com/football/competitions/{competition_id}/compseasons"
PULSE_FIXTURES_URL = "https://footballapi.pulselive.com/football/fixtures?compSeasons={season_id}&pageSize=1000"
PULSE_SQUAD_URL = "https://footballapi.pulselive.com/football/teams/{team_id}/compseasons/{season_id}/staff?altIds=true"
PULSE_PLAYER_URL = "https://footballapi.pulselive.com/football/players"
PULSE_PLAYER_STATS_URL = "https://footballapi.pulselive.com/football/stats/player/{player_id}?comps={competition_id}&compSeasons={season_id}&pageSize=100"
PULSE_MATCH_STATS_URL = "https://footballapi.pulselive.com/football/stats/match/{match_id}"
PULSE_TEXTSTREAM_URL = "https://footballapi.pulselive.com/football/fixtures/{match_id}/textstream/EN?pageSize=1000&sort=desc"

# Extract

def fetch_fpl_bootstrap() -> dict:
    """Fetch the FPL bootstrap payload."""
    response = HTTP_WORKER.get(FPL_BOOTSTRAP_URL, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()

def fetch_fpl_teams() -> list[dict]:
    """Fetch the raw FPL team list from bootstrap-static."""
    return fetch_fpl_bootstrap()["teams"]

def fetch_pulse_teams(pulse_team_id: int) -> dict:
    """Fetch data on teams from the PulseLive data."""
    url = f"https://footballapi.pulselive.com/football/teams/{pulse_team_id}"
    response = HTTP_WORKER.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()

def fetch_fpl_fixtures() -> list[dict]:
    """Fetch the raw FPL fixtures list."""
    response = HTTP_WORKER.get(FPL_FIXTURES_URL, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()

def fetch_fpl_live_stats(gameweek: int) -> dict:
    """Fetch live FPL event stats for a given gameweek."""
    url = FPL_LIVE_URL.format(gameweek=gameweek)
    response = HTTP_WORKER.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()

def fetch_pulse_comp_seasons(competition_id: int = 1) -> dict:
    """
    Fetch competition seasons from PulseLive.
    Only page 0 is populated, so we fetch once and return the payload.
    """
    url = PULSE_COMP_SEASONS_URL.format(competition_id=competition_id)
    response = HTTP_WORKER.get(
        url,
        params={"competitions": competition_id, "page": 0, "pageSize": 100},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()

def fetch_pulse_season_id(season_name: str, competition_id: int = 1) -> int:
    payload = fetch_pulse_comp_seasons(competition_id=competition_id)
    for season in payload.get("content", []):
        if str(season.get("label")) == season_name:
            return int(season.get("id"))
    raise ValueError(f"Could not resolve season_id for season_name={season_name}")

def fetch_pulse_season_name(season_id: int, competition_id: int = 1) -> str:
    """Resolve a season label like '2025/26' from the PulseLive comp seasons payload."""
    payload = fetch_pulse_comp_seasons(competition_id=competition_id)
    for season in payload.get("content", []):
        try:
            if int(season.get("id")) == int(season_id):
                label = season.get("label")
                if label:
                    return str(label)
        except (TypeError, ValueError):
            continue
    raise ValueError(f"Could not resolve season_name for season_id={season_id}")

def fetch_pulse_fixtures(season_id: int) -> dict:
    """Fetch all Premier League fixtures for a given season."""
    url = PULSE_FIXTURES_URL.format(season_id=season_id)
    response = HTTP_WORKER.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()

def fetch_pulse_squad(team_id: int, season_id: int) -> dict:
    """Fetch the squad/staff payload for one team in one season."""
    url = PULSE_SQUAD_URL.format(team_id=team_id, season_id=season_id)
    response = HTTP_WORKER.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()

def fetch_pulse_player_season_stats(
    player_id: int,
    season_id: int,
    competition_id: int = 1,
) -> dict:
    """Fetch PulseLive season stats for one player."""
    url = PULSE_PLAYER_STATS_URL.format(
        player_id=player_id, 
        competition_id=competition_id, 
        season_id=season_id
    )
    
    response = HTTP_WORKER.get(
        url,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()

def fetch_pulse_match_stats(match_id: int) -> dict:
    """Fetch team match stats for one fixture."""
    url = PULSE_MATCH_STATS_URL.format(match_id=match_id)
    response = HTTP_WORKER.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()

def fetch_pulse_players_list(season_id: int, page: int = 0) -> dict:
    """Fetch one page of Pulse players for a season."""
    response = HTTP_WORKER.get(
        PULSE_PLAYER_URL,
        params={
            "pageSize": 30,
            "compSeasons": season_id,
            "altIds": "true",
            "page": page,
            "type": "player",
            "id": -1,
            "compSeasonId": season_id,
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()

def fetch_pulse_textstream(match_id: int) -> dict:
    """Fetch the PulseLive textstream for one fixture."""
    url = PULSE_TEXTSTREAM_URL.format(match_id=match_id)
    response = HTTP_WORKER.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()

# Transform

def build_dim_seasons(season_id: int) -> pd.DataFrame:
    season_name = fetch_pulse_season_name(season_id)
    return pd.DataFrame(
        [
            {
                "season_id": int(season_id),
                "season_name": season_name
            }
        ]
    )

def build_dim_teams(teams: dict) -> pd.DataFrame:
    rows = []
    for team in teams:
        fpl_id = team.get("id")
        pulse_id = team.get("pulse_id")
        code = team.get("code")
        team_logo_url = None
        if code is not None:
            team_logo_url = f"https://resources.premierleague.com/premierleague/badges/50/t{code}.png"

        # Retrieve PulseLive data for the teams
        pulse_teams = fetch_pulse_teams(int(pulse_id))
        # Retrieve PulseLive names for teams
        team_name = pulse_teams.get("name", team.get("name"))
        club = pulse_teams.get("club", {})
        short_name = club.get("abbr", team.get("short_name"))
        grounds_lst = pulse_teams.get("grounds", [])
        primary_ground = grounds_lst[0] if grounds_lst else {}

        stadium = primary_ground.get("name")
        city = primary_ground.get("city")

        rows.append(
            {
                "team_id": int(pulse_id),
                "fpl_team_id": int(fpl_id),
                "team_name": str(team_name).strip(),
                "short_name": str(short_name).strip(),
                "stadium": str(stadium).strip() if stadium else None,
                "city": str(city).strip() if city else None,
                "team_logo_url": team_logo_url
            }
        )
    
    df = pd.DataFrame(rows)
    df = df.dropna(subset=["team_id"]).drop_duplicates(subset=["team_id"]).copy()
    df["team_id"] = df["team_id"].astype(int)

    cols = ["team_id", "fpl_team_id", "team_name", "short_name", "stadium", "city", "team_logo_url"]
    print(f"Final dim_teams created. Resolved {len(df)} teams.")
    return df[cols]

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

def build_pulse_squad_players_list(season_id: int, dim_teams: pd.DataFrame) -> pd.DataFrame:

    cols = [
        "pulse_player_id",
        "opta_code",
        "player_name",
        "country",
        "position",
        "position_info",
        "shirt_number",
        "current_team_id",
        "date_of_birth",
        "appearances",
    ]

    if dim_teams.empty:
        return pd.DataFrame(columns=cols)

    team_rows = (
        dim_teams[["team_id"]]
        .copy()
        .dropna(subset=["team_id"])
        .drop_duplicates(subset=["team_id"])
        .sort_values(by=["team_id"], kind="stable")
    )

    rows: list[dict] = []
    for team in tqdm(
        team_rows.itertuples(index=False),
        total=len(team_rows),
        desc="Building Pulse squad players",
        unit="team",
    ):
        team_id = int(team.team_id)

        try:
            payload = fetch_pulse_squad(team_id, season_id)
        except Exception:
            continue

        players = extract_squad_players(payload)
        if not players:
            continue

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

            rows.append(
                {
                    "pulse_player_id": int(pulse_player_id),
                    "opta_code": opta_code,
                    "player_name": player.get("name", {}).get("display"),
                    "country": national_team.get("country"),
                    "position": info.get("position"),
                    "position_info": info.get("positionInfo"),
                    "shirt_number": info.get("shirtNum"),
                    "current_team_id": team_id,
                    "date_of_birth": extract_birth_date_label(player),
                    "appearances": player.get("appearances"),
                }
            )

    df = pd.DataFrame(rows, columns=cols)
    if df.empty:
        return pd.DataFrame(columns=cols)

    df["pulse_player_id"] = pd.to_numeric(df["pulse_player_id"], errors="coerce").astype("Int64")
    df["current_team_id"] = pd.to_numeric(df["current_team_id"], errors="coerce").astype("Int64")
    df["shirt_number"] = pd.to_numeric(df["shirt_number"], errors="coerce").astype("Int64")
    df["appearances"] = pd.to_numeric(df["appearances"], errors="coerce").astype("Int64")

    for col in ["opta_code", "player_name", "country", "position", "position_info", "date_of_birth"]:
        df[col] = df[col].astype("string").str.strip()

    df = (
        df.dropna(subset=["pulse_player_id"])
        .drop_duplicates(subset=["pulse_player_id", "current_team_id"], keep="first")
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

def build_dim_fixtures(season_id: int) -> pd.DataFrame:

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

    # Build Pulse fixture_id -> FPL fixture id crosswalk from the official FPL payload.
    pulse_to_fpl_fixture: dict[int, int] = {}
    try:
        fpl_fixtures = fetch_fpl_fixtures()
    except Exception:
        fpl_fixtures = []

    if isinstance(fpl_fixtures, dict):
        fpl_fixtures = fpl_fixtures.get("content", []) or []

    if isinstance(fpl_fixtures, list):
        for fx in fpl_fixtures:
            try:
                fpl_fixture_id = fx.get("id")
                pulse_fixture_id = fx.get("pulse_id")
                if fpl_fixture_id is None or pulse_fixture_id is None:
                    continue
                pulse_to_fpl_fixture[int(pulse_fixture_id)] = int(fpl_fixture_id)
            except Exception:
                continue

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
        "player1_id",
        "player2_id",
        "minute",
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
            minute = format_minute(ev.get("time"))

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
            player1_id = None
            player2_id = None

            if "substitution" in raw_type:
                event_type = "substitution"
                player1_id = player_ids[0] if len(player_ids) >= 1 else None
                player2_id = player_ids[1] if len(player_ids) >= 2 else None
            elif "yellow" in raw_type:
                event_type = "yellow"
                player1_id = player_ids[0] if len(player_ids) >= 1 else None
            elif "red" in raw_type:
                event_type = "red"
                player1_id = player_ids[0] if len(player_ids) >= 1 else None
            elif "goal" in raw_type:
                if "own goal" in raw_type or "own goal" in normalize_text(text):
                    event_type = "own goal"
                elif "penalty" in raw_type or "penalty" in normalize_text(text):
                    event_type = "penalty goal"
                else:
                    event_type = "goal"
                player1_id = player_ids[0] if len(player_ids) >= 1 else None
                player2_id = player_ids[1] if len(player_ids) >= 2 else None
            else:
                continue

            if player1_id is None:
                continue

            rows.append(
                {
                    "fixture_id": fixture_id,
                    "season_id": season_id,
                    "team_id": team_id,
                    "event_type": event_type,
                    "player1_id": player1_id,
                    "player2_id": player2_id,
                    "minute": minute,
                    "__minute_sort": minute_sort_key(minute),
                    "__event_priority": match_event_priority(event_type),
                }
            )

    if not rows:
        return pd.DataFrame(columns=output_cols)

    out = pd.DataFrame(rows)
    business_cols = [c for c in output_cols if c != "match_event_id"]
    out = (
        out.drop_duplicates(subset=business_cols, keep="first")
        .sort_values(
            by=["fixture_id", "__minute_sort", "__event_priority", "team_id", "player1_id", "player2_id"],
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
    out["event_type"] = out["event_type"].astype("string")
    out["minute"] = out["minute"].astype("string")

    out.insert(0, "match_event_id", [f"event_{i}" for i in range(len(out))])
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
            minute = format_minute(ev.get("time"))

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

            rows.append(
                {
                    "fixture_id": fixture_id,
                    "season_id": season_id,
                    "team_id": team_id,
                    "player1_id": player_ids[0],
                    "player2_id": player_ids[1] if len(player_ids) > 1 else None,
                    "minute": minute,
                    "shot_type": infer_shot_type(event_type_l, text_lc),
                    "body_part": infer_body_part(text_lc),
                    "distance": infer_distance(text_lc),
                    "outcome": infer_shot_outcome(event_type_l),
                    "__minute_sort": minute_sort_key(minute),
                    "__event_idx": event_idx,
                }
            )

    if not rows:
        return pd.DataFrame(columns=output_cols)

    out = pd.DataFrame(rows)
    business_cols = [c for c in output_cols if c != "shot_event_id"]
    out = (
        out.drop_duplicates(subset=business_cols, keep="first")
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
    out["minute"] = out["minute"].astype("string")
    out["shot_type"] = out["shot_type"].astype("string")
    out["body_part"] = out["body_part"].astype("string")
    out["distance"] = out["distance"].astype("string")
    out["outcome"] = out["outcome"].astype("string")

    out.insert(0, "shot_event_id", [f"shot_{i}" for i in range(len(out))])
    out["shot_event_id"] = out["shot_event_id"].astype("string")

    return out[output_cols].reset_index(drop=True)

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

    def long_obs(df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame(columns=["player_id", "fixture_id", "team_id"])
        if not {"fixture_id", "team_id"}.issubset(df.columns):
            return pd.DataFrame(columns=["player_id", "fixture_id", "team_id"])

        parts = []
        for player_col in ("player1_id", "player2_id"):
            if player_col not in df.columns:
                continue
            tmp = df[["fixture_id", "team_id", player_col]].copy()
            tmp = tmp.rename(columns={player_col: "player_id"})
            parts.append(tmp)

        if not parts:
            return pd.DataFrame(columns=["player_id", "fixture_id", "team_id"])

        out = pd.concat(parts, ignore_index=True)
        for col in ("player_id", "fixture_id", "team_id"):
            out[col] = pd.to_numeric(out[col], errors="coerce").astype("Int64")

        out = out.dropna(subset=["player_id", "fixture_id", "team_id"]).copy()
        out = out[out["team_id"].isin(valid_team_ids)].copy()
        return out.drop_duplicates(subset=["player_id", "fixture_id", "team_id"], keep="first")

    observations = pd.concat(
        [
            long_obs(fact_match_events),
            long_obs(fact_shot_events),
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

    bridge.insert(0, "bridge_player_season_id", range(1, len(bridge) + 1))

    for col in [
        "bridge_player_season_id",
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

    # FPL player id -> Pulse player id
    fpl_players = dim_players[["pulse_player_id", "fpl_player_id"]].copy()
    fpl_players["pulse_player_id"] = pd.to_numeric(fpl_players["pulse_player_id"], errors="coerce").astype("Int64")
    fpl_players["fpl_player_id"] = pd.to_numeric(fpl_players["fpl_player_id"], errors="coerce").astype("Int64")
    fpl_players = (
        fpl_players.dropna(subset=["pulse_player_id", "fpl_player_id"])
        .drop_duplicates(subset=["fpl_player_id"], keep="first")
        .reset_index(drop=True)
    )
    fpl_to_pulse_player = {
        int(row.fpl_player_id): int(row.pulse_player_id)
        for row in fpl_players.itertuples(index=False)
    }

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

    rows: list[dict] = []
    seen_keys: set[tuple[int, int]] = set()

    max_gameweek = int(pd.to_numeric(dim_fixtures["gameweek"], errors="coerce").dropna().max())

    for gw in tqdm(range(1, max_gameweek + 1), desc="Building fact_match_lineup", unit="gw"):
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

    fpl_players = dim_players[["pulse_player_id", "fpl_player_id"]].copy()
    fpl_players["pulse_player_id"] = pd.to_numeric(fpl_players["pulse_player_id"], errors="coerce").astype("Int64")
    fpl_players["fpl_player_id"] = pd.to_numeric(fpl_players["fpl_player_id"], errors="coerce").astype("Int64")
    fpl_players = (
        fpl_players.dropna(subset=["pulse_player_id", "fpl_player_id"])
        .drop_duplicates(subset=["fpl_player_id"], keep="first")
        .reset_index(drop=True)
    )

    fpl_to_pulse_player = {
        int(row.fpl_player_id): int(row.pulse_player_id)
        for row in fpl_players.itertuples(index=False)
    }

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
            ["fixture_id", "player1_id"],
        ]
        .copy()
    )
    if not penalty_patch.empty:
        penalty_patch["fixture_id"] = pd.to_numeric(penalty_patch["fixture_id"], errors="coerce").astype("Int64")
        penalty_patch["player1_id"] = pd.to_numeric(penalty_patch["player1_id"], errors="coerce").astype("Int64")
        penalty_patch = (
            penalty_patch.dropna(subset=["fixture_id", "player1_id"])
            .groupby(["player1_id", "fixture_id"], as_index=False)
            .size()
            .rename(columns={"size": "penalties_scored_patch"})
        )

        out = out.merge(
            penalty_patch,
            left_on=["player_id", "fixture_id"],
            right_on=["player1_id", "fixture_id"],
            how="left",
            validate="1:1",
        ).drop(columns=["player1_id"], errors="ignore")
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
    output_cols = ["player_id", "season_id", *PLAYER_SEASON_ENDPOINT_COLS, *PLAYER_FIXTURE_DERIVED_COLS]

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

def run_pipeline(season_id: int) -> dict[str, pd.DataFrame]:
    """
    Build the full ETL pipeline for a single season and return every
    intermediate/final dataframe in a stable dictionary.
    """

    print("\nBuilding dim_seasons...")
    dim_seasons = build_dim_seasons(season_id)
    print("\nBuilding dim_teams...")
    dim_teams = build_dim_teams(fetch_fpl_teams())
    print("\nBuilding dim_players...")
    dim_players = build_dim_players(season_id, dim_teams)
    print("\nBuilding dim_fixtures...")
    dim_fixtures = build_dim_fixtures(season_id)

    print("\nBuilding fact_match_events...")
    fact_match_events = build_fact_match_events(dim_fixtures, dim_teams, dim_players)
    print("\nBuilding fact_shot_events...")
    fact_shot_events = build_fact_shot_events(dim_fixtures, dim_teams, dim_players)

    print("\nBuilding bridge_player_seasons...")
    bridge_player_seasons = build_bridge_player_seasons(
        dim_players=dim_players,
        fact_match_events=fact_match_events,
        fact_shot_events=fact_shot_events,
        dim_fixtures=dim_fixtures,
        dim_teams=dim_teams,
    )

    print("\nBuilding fact_match_lineup...")
    fact_match_lineup = build_fact_match_lineup(
        dim_players=dim_players,
        bridge_player_seasons=bridge_player_seasons,
        dim_fixtures=dim_fixtures,
    )

    print("\nBuilding fact_player_fixture_stats...")
    fact_player_fixture_stats = build_fact_player_fixture_stats(
        fact_match_lineup=fact_match_lineup,
        dim_players=dim_players,
        dim_fixtures=dim_fixtures,
        fact_match_events=fact_match_events,
    )

    print("\nBuilding fact_team_match_stats...")
    fact_team_match_stats = build_fact_team_match_stats(
        dim_fixtures=dim_fixtures,
        fact_player_fixture_stats=fact_player_fixture_stats,
    )

    print("\nBuilding fact_player_season_stats...")
    fact_player_season_stats = build_fact_player_season_stats(
        dim_players=dim_players,
        fact_player_fixture_stats=fact_player_fixture_stats,
        season_id=season_id,
    )

    print("\nBuilding fact_team_season_stats...")
    fact_team_season_stats = build_fact_team_season_stats(
        fact_team_match_stats=fact_team_match_stats,
    )

    print("\nBuilding fact_premier_league_table...")
    fact_premier_league_table = build_fact_premier_league_table(
        fact_team_match_stats=fact_team_match_stats,
    )

    return {
        "dim_seasons": dim_seasons,
        "dim_teams": dim_teams,
        "dim_players": dim_players,
        "dim_fixtures": dim_fixtures,
        "fact_match_events": fact_match_events,
        "fact_shot_events": fact_shot_events,
        "bridge_player_seasons": bridge_player_seasons,
        "fact_match_lineup": fact_match_lineup,
        "fact_team_match_stats": fact_team_match_stats,
        "fact_player_season_stats": fact_player_season_stats,
        "fact_team_season_stats": fact_team_season_stats,
        "fact_premier_league_table": fact_premier_league_table,
    }

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the season ETL pipeline smoke test.")
    parser.add_argument(
        "--season-id",
        type=int,
        default=777,
        help="Pulse competition season id to build.",
    )
    parser.add_argument(
        "--export-snapshots",
        action="store_true",
        help="Write a versioned CSV snapshot after the pipeline finishes.",
    )
    parser.add_argument(
        "--snapshot-base-dir",
        type=str,
        default="data/snapshots",
        help="Base directory for CSV snapshot runs.",
    )
    args = parser.parse_args(argv)

    frames = run_pipeline(args.season_id)

    print(f"Pipeline completed for season_id={args.season_id}")
    for name in [
        "dim_seasons",
        "dim_teams",
        "dim_players",
        "dim_fixtures",
        "fact_match_events",
        "fact_shot_events",
        "bridge_player_seasons",
        "fact_match_lineup",
        "fact_team_match_stats",
        "fact_player_season_stats",
        "fact_team_season_stats",
        "fact_premier_league_table",
    ]:
        print_frame_summary(name, frames[name])

    if args.export_snapshots:
        snapshot_dir = export_csv_snapshot(
            frames=frames,
            season_id=args.season_id,
            base_dir=args.snapshot_base_dir,
        )
        print(f"Snapshot written to {snapshot_dir}")


if __name__ == "__main__":
    main()