from typing import Any
from sqlalchemy import text
from sqlalchemy.engine import Connection

WAREHOUSE = "warehouse"

def _rows(result) -> list[dict[str, Any]]:
    return [dict(row._mapping) for row in result]

def count_rows(conn: Connection, table_name: str) -> int:
    query = text(f"SELECT COUNT(*) FROM {WAREHOUSE}.{table_name}")
    return int(conn.execute(query).scalar_one())

def fetch_latest_season(conn: Connection) -> tuple[int | None, str | None]:
    query = text(f"""
        SELECT season_id, season_name
        FROM {WAREHOUSE}.dim_seasons
        ORDER BY season_id DESC
        LIMIT 1
    """)
    row = conn.execute(query).mappings().first()
    return (row["season_id"], row["season_name"]) if row else (None, None)