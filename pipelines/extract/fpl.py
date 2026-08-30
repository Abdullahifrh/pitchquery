from pipelines.extract.pulse import HTTP_WORKER

REQUEST_TIMEOUT = 10

FPL_BOOTSTRAP_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"
FPL_FIXTURES_URL = "https://fantasy.premierleague.com/api/fixtures/"
FPL_LIVE_URL = "https://fantasy.premierleague.com/api/event/{gameweek}/live/"

def fetch_fpl_bootstrap() -> dict:
    """Fetch the FPL bootstrap payload."""
    response = HTTP_WORKER.get(FPL_BOOTSTRAP_URL, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()

def fetch_fpl_teams() -> list[dict]:
    """Fetch the raw FPL team list from bootstrap-static."""
    return fetch_fpl_bootstrap()["teams"]

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

