from sqlalchemy import text
from sqlalchemy.engine import Connection
from api.repositories.base import WAREHOUSE, _rows

def fetch_players(conn: Connection):
    return _rows(conn.execute(text(f"SELECT * FROM {WAREHOUSE}.dim_players ORDER BY player_name")))

def fetch_player(conn: Connection, player_id: int):
    row = conn.execute(text(f"SELECT * FROM {WAREHOUSE}.dim_players WHERE player_id = :pid"), {"pid": player_id}).mappings().first()
    return dict(row) if row else None

def fetch_player_seasons(conn: Connection, player_id: int):
    return _rows(conn.execute(text(f"SELECT * FROM {WAREHOUSE}.bridge_player_seasons WHERE player_id = :pid"), {"pid": player_id}))

def fetch_player_season_stats(conn: Connection, player_id: int, season_id: int):
    query = text(f"SELECT * FROM {WAREHOUSE}.fact_player_season_stats WHERE player_id = :pid AND season_id = :sid")
    row = conn.execute(query, {"pid": player_id, "sid": season_id}).mappings().first()
    return dict(row) if row else None