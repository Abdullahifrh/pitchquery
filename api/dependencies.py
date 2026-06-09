from fastapi import HTTPException
from sqlalchemy.engine import Connection
from api.repositories.base import fetch_latest_season

def resolve_season_id(conn: Connection, season_id: int | None) -> int:
    if season_id is not None:
        return season_id
    latest, _ = fetch_latest_season(conn)
    if latest is None:
        raise HTTPException(status_code=503, detail="No season available.")
    return latest