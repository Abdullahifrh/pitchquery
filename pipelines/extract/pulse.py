import requests

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
_adapter = requests.adapters.HTTPAdapter(pool_connections=100, pool_maxsize=100)
HTTP_WORKER.mount("https://", _adapter)
HTTP_WORKER.mount("http://", _adapter)

PULSE_COMP_SEASONS_URL = "https://footballapi.pulselive.com/football/competitions/{competition_id}/compseasons"
PULSE_FIXTURES_URL = "https://footballapi.pulselive.com/football/fixtures?compSeasons={season_id}&pageSize=1000"
PULSE_SQUAD_URL = "https://footballapi.pulselive.com/football/teams/{team_id}/compseasons/{season_id}/staff?altIds=true"
PULSE_PLAYER_URL = "https://footballapi.pulselive.com/football/players"
PULSE_PLAYER_STATS_URL = "https://footballapi.pulselive.com/football/stats/player/{player_id}?comps={competition_id}&compSeasons={season_id}&pageSize=100"
PULSE_MATCH_STATS_URL = "https://footballapi.pulselive.com/football/stats/match/{match_id}"
PULSE_TEXTSTREAM_URL = "https://footballapi.pulselive.com/football/fixtures/{match_id}/textstream/EN?pageSize=1000&sort=desc"

def fetch_pulse_teams(pulse_team_id: int) -> dict:
    """Fetch data on teams from the PulseLive data."""
    url = f"https://footballapi.pulselive.com/football/teams/{pulse_team_id}"
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

