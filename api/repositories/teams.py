from sqlalchemy import text
from sqlalchemy.engine import Connection
from api.repositories.base import WAREHOUSE, _rows

def fetch_teams(conn: Connection):
    return _rows(conn.execute(text(f"SELECT * FROM {WAREHOUSE}.dim_teams ORDER BY team_name")))

def fetch_team(conn: Connection, team_id: int):
    return dict(conn.execute(text(f"SELECT * FROM {WAREHOUSE}.dim_teams WHERE team_id = :tid"), {"tid": team_id}).mappings().first() or {})

def fetch_team_players(conn: Connection, team_id: int, season_id: int):
    query = text(f"""
        SELECT DISTINCT p.* FROM {WAREHOUSE}.dim_players p
        JOIN {WAREHOUSE}.bridge_player_seasons b ON b.player_id = p.player_id
        WHERE b.team_id = :team_id AND b.season_id = :season_id
    """)
    return _rows(conn.execute(query, {"team_id": team_id, "season_id": season_id}))

def fetch_team_season_stats(conn: Connection, team_id: int, season_id: int):
    query = text(f"SELECT * FROM {WAREHOUSE}.fact_team_season_stats WHERE team_id = :tid AND season_id = :sid")
    row = conn.execute(query, {"tid": team_id, "sid": season_id}).mappings().first()
    return dict(row) if row else None

def fetch_team_match_stats(conn: Connection, team_id: int, season_id: int):
    query = text(f"""
        SELECT *
        FROM {WAREHOUSE}.fact_team_match_stats
        WHERE team_id = :tid AND season_id = :sid
        ORDER BY fixture_id
    """)
    return _rows(conn.execute(query, {"tid": team_id, "sid": season_id}))

def fetch_standings(conn: Connection, season_id: int):
    query = text(f"""
        SELECT * FROM {WAREHOUSE}.fact_premier_league_table s
        WHERE s.season_id = :sid ORDER BY s.points DESC
    """)
    return _rows(conn.execute(query, {"sid": season_id}))