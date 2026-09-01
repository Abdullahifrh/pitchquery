import pandas as pd
import pytest

# ---------------------------------------------------------------------
# sample_frames: one small, internally-consistent mini-season
# ---------------------------------------------------------------------

SAMPLE_SEASON_ID = 841

def _build_sample_frames() -> dict[str, pd.DataFrame]:
    """Four teams, six players, a round-robin's worth of fixtures (some
    completed, some not), and matching fact/bridge rows. Small enough to
    read at a glance, big enough to genuinely exercise every PK/FK/
    business-rule check in the schema contract without hardcoding a real
    season's exact scale (e.g. "always 20 teams") into a synthetic
    fixture that has nothing to do with 20.
    """
    now = pd.Timestamp.now("UTC")

    dim_seasons = pd.DataFrame([
        {"season_id": SAMPLE_SEASON_ID, "season_name": "2026/27", "competition_name": "Premier League"},
    ])

    dim_teams = pd.DataFrame([
        {"team_id": 1, "team_name": "Sample Arsenal", "short_name": "ARS", "team_logo_url": "https://example.com/1.png"},
        {"team_id": 2, "team_name": "Sample Villa", "short_name": "AVL", "team_logo_url": "https://example.com/2.png"},
        {"team_id": 3, "team_name": "Sample Chelsea", "short_name": "CHE", "team_logo_url": "https://example.com/3.png"},
        {"team_id": 4, "team_name": "Sample Brentford", "short_name": "BRE", "team_logo_url": "https://example.com/4.png"},
    ])

    dim_players = pd.DataFrame([
        {"player_id": 10, "player_name": "Player Ten", "date_of_birth": "1998-01-01", "country": "England", "player_photo_url": "https://example.com/p10.png"},
        {"player_id": 11, "player_name": "Player Eleven", "date_of_birth": "1997-02-02", "country": "England", "player_photo_url": "https://example.com/p11.png"},
        {"player_id": 20, "player_name": "Player Twenty", "date_of_birth": "1999-03-03", "country": "Wales", "player_photo_url": "https://example.com/p20.png"},
        {"player_id": 21, "player_name": "Player Twenty-One", "date_of_birth": "1996-04-04", "country": "Wales", "player_photo_url": "https://example.com/p21.png"},
        {"player_id": 30, "player_name": "Player Thirty", "date_of_birth": "2000-05-05", "country": "Scotland", "player_photo_url": "https://example.com/p30.png"},
        {"player_id": 40, "player_name": "Player Forty", "date_of_birth": "1995-06-06", "country": "Ireland", "player_photo_url": "https://example.com/p40.png"},
    ])

    dim_fixtures = pd.DataFrame([
        {"fixture_id": 100, "season_id": SAMPLE_SEASON_ID, "gameweek": 1, "kickoff_datetime": "2026-08-21 19:00:00", "stadium": "Sample Stadium A", "attendance": 60000, "home_team_id": 1, "away_team_id": 2, "fixture_status": "C"},
        {"fixture_id": 101, "season_id": SAMPLE_SEASON_ID, "gameweek": 1, "kickoff_datetime": "2026-08-22 14:00:00", "stadium": "Sample Stadium B", "attendance": 40000, "home_team_id": 3, "away_team_id": 4, "fixture_status": "C"},
        {"fixture_id": 102, "season_id": SAMPLE_SEASON_ID, "gameweek": 2, "kickoff_datetime": "2026-08-29 15:00:00", "stadium": "Sample Stadium C", "attendance": 55000, "home_team_id": 2, "away_team_id": 3, "fixture_status": "U"},
    ])

    bridge_player_seasons = pd.DataFrame([
        {"bridge_player_season_id": "b10", "player_id": 10, "season_id": SAMPLE_SEASON_ID, "team_id": 1, "position": "FWD", "position_info": None, "shirt_number": 9, "age": 28, "transfer_sequence": 1},
        {"bridge_player_season_id": "b11", "player_id": 11, "season_id": SAMPLE_SEASON_ID, "team_id": 1, "position": "MID", "position_info": None, "shirt_number": 8, "age": 29, "transfer_sequence": 1},
        {"bridge_player_season_id": "b20", "player_id": 20, "season_id": SAMPLE_SEASON_ID, "team_id": 2, "position": "DEF", "position_info": None, "shirt_number": 4, "age": 27, "transfer_sequence": 1},
        {"bridge_player_season_id": "b21", "player_id": 21, "season_id": SAMPLE_SEASON_ID, "team_id": 2, "position": "GK", "position_info": None, "shirt_number": 1, "age": 30, "transfer_sequence": 1},
        {"bridge_player_season_id": "b30", "player_id": 30, "season_id": SAMPLE_SEASON_ID, "team_id": 3, "position": "MID", "position_info": None, "shirt_number": 10, "age": 26, "transfer_sequence": 1},
        {"bridge_player_season_id": "b40", "player_id": 40, "season_id": SAMPLE_SEASON_ID, "team_id": 4, "position": "FWD", "position_info": None, "shirt_number": 7, "age": 31, "transfer_sequence": 1},
    ])

    fact_match_lineup = pd.DataFrame([
        {"player_id": 10, "fixture_id": 100, "season_id": SAMPLE_SEASON_ID, "team_id": 1, "minutes_played": 90, "starter_flag": True},
        {"player_id": 11, "fixture_id": 100, "season_id": SAMPLE_SEASON_ID, "team_id": 1, "minutes_played": 78, "starter_flag": True},
        {"player_id": 20, "fixture_id": 100, "season_id": SAMPLE_SEASON_ID, "team_id": 2, "minutes_played": 90, "starter_flag": True},
        {"player_id": 21, "fixture_id": 100, "season_id": SAMPLE_SEASON_ID, "team_id": 2, "minutes_played": 90, "starter_flag": True},
        {"player_id": 30, "fixture_id": 101, "season_id": SAMPLE_SEASON_ID, "team_id": 3, "minutes_played": 65, "starter_flag": True},
        {"player_id": 40, "fixture_id": 101, "season_id": SAMPLE_SEASON_ID, "team_id": 4, "minutes_played": 90, "starter_flag": True},
    ])

    fact_player_season_stats = pd.DataFrame([
        {"player_id": 10, "season_id": SAMPLE_SEASON_ID, "goals": 2, "assists": 1},
        {"player_id": 11, "season_id": SAMPLE_SEASON_ID, "goals": 0, "assists": 2},
        {"player_id": 20, "season_id": SAMPLE_SEASON_ID, "goals": 0, "assists": 0},
        {"player_id": 21, "season_id": SAMPLE_SEASON_ID, "goals": 0, "assists": 0},
        {"player_id": 30, "season_id": SAMPLE_SEASON_ID, "goals": 1, "assists": 0},
        {"player_id": 40, "season_id": SAMPLE_SEASON_ID, "goals": 1, "assists": 1},
    ])

    fact_team_match_stats = pd.DataFrame([
        {"fixture_id": 100, "season_id": SAMPLE_SEASON_ID, "team_id": 1, "is_home": True, "goals_scored": 2, "goals_conceded": 0},
        {"fixture_id": 100, "season_id": SAMPLE_SEASON_ID, "team_id": 2, "is_home": False, "goals_scored": 0, "goals_conceded": 2},
        {"fixture_id": 101, "season_id": SAMPLE_SEASON_ID, "team_id": 3, "is_home": True, "goals_scored": 1, "goals_conceded": 1},
        {"fixture_id": 101, "season_id": SAMPLE_SEASON_ID, "team_id": 4, "is_home": False, "goals_scored": 1, "goals_conceded": 1},
    ])

    fact_team_season_stats = pd.DataFrame([
        {"team_id": 1, "season_id": SAMPLE_SEASON_ID, "played": 1, "won": 1, "drawn": 0, "lost": 0},
        {"team_id": 2, "season_id": SAMPLE_SEASON_ID, "played": 1, "won": 0, "drawn": 0, "lost": 1},
        {"team_id": 3, "season_id": SAMPLE_SEASON_ID, "played": 1, "won": 0, "drawn": 1, "lost": 0},
        {"team_id": 4, "season_id": SAMPLE_SEASON_ID, "played": 1, "won": 0, "drawn": 1, "lost": 0},
    ])

    fact_match_events = pd.DataFrame([
        {"match_event_id": "fixture_100_event_0", "fixture_id": 100, "season_id": SAMPLE_SEASON_ID, "team_id": 1, "event_type": "goal", "scorer_player_id": 10, "assist_player_id": None, "own_goal_player_id": None, "carded_player_id": None, "player_on_id": None, "player_off_id": None, "minute": 23, "minute_display": "23", "is_stoppage_time": False},
        {"match_event_id": "fixture_100_event_1", "fixture_id": 100, "season_id": SAMPLE_SEASON_ID, "team_id": 1, "event_type": "goal", "scorer_player_id": 10, "assist_player_id": 11, "own_goal_player_id": None, "carded_player_id": None, "player_on_id": None, "player_off_id": None, "minute": 67, "minute_display": "67", "is_stoppage_time": False},
        {"match_event_id": "fixture_101_event_0", "fixture_id": 101, "season_id": SAMPLE_SEASON_ID, "team_id": 3, "event_type": "goal", "scorer_player_id": 30, "assist_player_id": None, "own_goal_player_id": None, "carded_player_id": None, "player_on_id": None, "player_off_id": None, "minute": 12, "minute_display": "12", "is_stoppage_time": False},
    ])

    fact_shot_events = pd.DataFrame([
        {"shot_event_id": "fixture_100_shot_0", "fixture_id": 100, "season_id": SAMPLE_SEASON_ID, "team_id": 1, "player1_id": 10, "player2_id": None, "minute": 23, "minute_display": "23", "is_stoppage_time": False, "shot_type": "Open Play", "body_part": "Right Foot", "distance": "Inside Box", "outcome": "Goal"},
        {"shot_event_id": "fixture_101_shot_0", "fixture_id": 101, "season_id": SAMPLE_SEASON_ID, "team_id": 4, "player1_id": 40, "player2_id": None, "minute": 91, "minute_display": "90+1'", "is_stoppage_time": True, "shot_type": "Open Play", "body_part": "Head", "distance": "Outside Box", "outcome": "Saved"},
    ])

    fact_premier_league_table = pd.DataFrame([
        {"team_id": 1, "season_id": SAMPLE_SEASON_ID, "points": 3, "played": 1, "goal_difference": 2},
        {"team_id": 3, "season_id": SAMPLE_SEASON_ID, "points": 1, "played": 1, "goal_difference": 0},
        {"team_id": 4, "season_id": SAMPLE_SEASON_ID, "points": 1, "played": 1, "goal_difference": 0},
        {"team_id": 2, "season_id": SAMPLE_SEASON_ID, "points": 0, "played": 1, "goal_difference": -2},
    ])

    frames = {
        "dim_seasons": dim_seasons,
        "dim_teams": dim_teams,
        "dim_players": dim_players,
        "dim_fixtures": dim_fixtures,
        "bridge_player_seasons": bridge_player_seasons,
        "fact_match_lineup": fact_match_lineup,
        "fact_player_season_stats": fact_player_season_stats,
        "fact_team_match_stats": fact_team_match_stats,
        "fact_team_season_stats": fact_team_season_stats,
        "fact_match_events": fact_match_events,
        "fact_shot_events": fact_shot_events,
        "fact_premier_league_table": fact_premier_league_table,
    }

    # Stamp audit columns exactly like a real materialize_frames() call
    # would, so `frames` here is genuinely "already materialized"
    # shape — consistent with what test_pipeline.py's tests expect.
    for name, df in frames.items():
        df["ingested_at"] = now
        df["updated_at"] = now

    return frames

@pytest.fixture(scope="session")
def season_id() -> int:
    return SAMPLE_SEASON_ID


@pytest.fixture(scope="session")
def sample_frames() -> dict[str, pd.DataFrame]:
    """Session-scoped since it's read-only in every test that uses it —
    no test mutates these DataFrames in place (pandas assignment inside
    a test always rebinds a name / uses .copy(), never `df[...] = ...`
    on this fixture's frames directly)."""
    return _build_sample_frames()

# ---------------------------------------------------------------------
# Audit-harmonization raw fixture (pre-audit-column CSV shape)
# ---------------------------------------------------------------------

@pytest.fixture
def raw_team_frame_missing_audit_columns() -> pd.DataFrame:
    """The exact shape of a row from a pre-audit-column historical CSV
    (e.g. `data/historical/2025_26/dim_teams.csv`): no `ingested_at`/
    `updated_at` at all. Function-scoped since `test_warehouse.py`
    mutates the team_id/team_name per-test to avoid collisions.
    """
    return pd.DataFrame([
        {"team_id": 1, "team_name": "Harmonized United", "short_name": "HAR", "team_logo_url": "https://example.com/logo.png"}
    ])

# ---------------------------------------------------------------------
# In-memory SQLite engine
# ---------------------------------------------------------------------

@pytest.fixture
def sqlite_engine():
    """A fresh in-memory SQLite engine per test.

    `StaticPool` is required here, not optional: SQLAlchemy's default
    pooling opens a *new* SQLite connection per checkout, and a plain
    `sqlite:///:memory:` in-memory database only exists for the
    lifetime of a single connection — without StaticPool, a table
    created in one `engine.begin()` block would already be gone by the
    time the next one runs. StaticPool pins the engine to one
    connection for its whole lifetime, which is exactly what a
    function-scoped, single-test-lifetime engine needs.

    Foreign-key enforcement is OFF by default in SQLite (unlike
    Postgres, which always enforces it) — turned on here via a
    connection-level `PRAGMA`, so a test creating rows that violate a
    real FK constraint (see `pipelines.schema.FOREIGN_KEYS`) actually
    fails the way it would against the real warehouse, instead of
    silently succeeding.
    """
    sqlalchemy = pytest.importorskip("sqlalchemy")
    from sqlalchemy import event
    from sqlalchemy.pool import StaticPool

    engine = sqlalchemy.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_sqlite_fk_enforcement(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    try:
        yield engine
    finally:
        engine.dispose()

# ---------------------------------------------------------------------
# FastAPI TestClient, backed by a fake DB connection
# ---------------------------------------------------------------------

class _FakeResult:
    """Minimal stand-in for a SQLAlchemy `CursorResult`."""

    def __init__(self, rows: list[dict]):
        self._rows = rows

    def scalar_one(self):
        return self._rows[0][next(iter(self._rows[0]))] if self._rows else None

    def scalar(self):
        return self.scalar_one() if self._rows else None

    def mappings(self):
        from types import SimpleNamespace
        return SimpleNamespace(first=lambda: (self._rows[0] if self._rows else None))

    def __iter__(self):
        from types import SimpleNamespace
        return iter(SimpleNamespace(_mapping=row) for row in self._rows)

class FakeConnection:
    """Fakes just enough of `sqlalchemy.engine.Connection.execute` to
    drive `api/repositories/*.py`, by matching distinctive substrings
    of the compiled SQL rather than parsing it.
    """

    def __init__(self, data: dict):
        self.data = data

    def execute(self, query, params=None):
        sql = " ".join(str(query).lower().split())
        params = params or {}

        if sql.startswith("select count(*)"):
            table = next(t for t in self.data["counts"] if t in sql)
            return _FakeResult([{"count": self.data["counts"][table]}])

        if "select season_id, season_name" in sql:
            return _FakeResult(self.data["seasons"])

        if "dim_fixtures" in sql and "where fixture_id = :fid" in sql:
            fid = params.get("fid")
            return _FakeResult([r for r in self.data["fixtures"] if r["fixture_id"] == fid])

        if "dim_fixtures" in sql:
            return _FakeResult(self.data["fixtures"])

        if "fact_premier_league_table" in sql:
            return _FakeResult(self.data["standings"])

        if "dim_players" in sql and "where player_id = :pid" in sql:
            pid = params.get("pid")
            return _FakeResult([r for r in self.data["players"] if r["player_id"] == pid])

        if "dim_players" in sql:
            return _FakeResult(self.data["players"])

        return _FakeResult([])

@pytest.fixture
def fake_api_data() -> dict:
    from datetime import date, datetime
    return {
        "counts": {"dim_seasons": 1, "dim_teams": 20, "dim_players": 500, "dim_fixtures": 380},
        "seasons": [{"season_id": SAMPLE_SEASON_ID, "season_name": "2026/27"}],
        "fixtures": [
            {
                "fixture_id": 100,
                "season_id": SAMPLE_SEASON_ID,
                "gameweek": 1,
                "kickoff_datetime": datetime(2026, 8, 21, 19, 0, 0),
                "stadium": "Emirates Stadium",
                "attendance": 60000,
                "home_team_id": 1,
                "away_team_id": 2,
                "fixture_status": "C",
            }
        ],
        "standings": [
            {"team_id": 1, "season_id": SAMPLE_SEASON_ID, "points": 10},
            {"team_id": 2, "season_id": SAMPLE_SEASON_ID, "points": 7},
        ],
        "players": [
            {
                "player_id": 10,
                "player_name": "Test Player",
                "date_of_birth": date(1998, 1, 1),
                "country": "England",
                "player_photo_url": "https://example.com/p10.png",
            }
        ],
    }

@pytest.fixture
def client(fake_api_data):
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    TestClient = fastapi_testclient.TestClient

    from api.main import app
    from api.db import get_connection

    def _fake_get_connection():
        yield FakeConnection(fake_api_data)

    app.dependency_overrides[get_connection] = _fake_get_connection
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
