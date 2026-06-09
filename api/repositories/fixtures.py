from sqlalchemy import text
from sqlalchemy.engine import Connection
from api.repositories.base import WAREHOUSE, _rows

def fetch_fixtures(conn: Connection, season_id: int, gameweek: int | None, status: str | None):
    query = text(f"""
        SELECT * FROM {WAREHOUSE}.dim_fixtures 
        WHERE season_id = :sid 
        AND (:gw IS NULL OR gameweek = :gw) 
        AND (:st IS NULL OR fixture_status = :st)
    """)
    return _rows(conn.execute(query, {"sid": season_id, "gw": gameweek, "st": status}))

def fetch_fixture(conn: Connection, fixture_id: int):
    row = conn.execute(text(f"SELECT * FROM {WAREHOUSE}.dim_fixtures WHERE fixture_id = :fid"), {"fid": fixture_id}).mappings().first()
    return dict(row) if row else None

def fetch_fixture_events(conn: Connection, fixture_id: int):
    query = text(f"""
        SELECT *
        FROM {WAREHOUSE}.fact_match_events
        WHERE fixture_id = :fid
        ORDER BY minute NULLS LAST, match_event_id
    """)
    return _rows(conn.execute(query, {"fid": fixture_id}))

def fetch_fixture_shots(conn: Connection, fixture_id: int):
    query = text(f"""
        SELECT *
        FROM {WAREHOUSE}.fact_shot_events
        WHERE fixture_id = :fid
        ORDER BY minute NULLS LAST, shot_event_id
    """)
    return _rows(conn.execute(query, {"fid": fixture_id}))

def fetch_fixture_lineup(conn: Connection, fixture_id: int):
    return _rows(conn.execute(text(f"SELECT * FROM {WAREHOUSE}.fact_match_lineup WHERE fixture_id = :fid"), {"fid": fixture_id}))