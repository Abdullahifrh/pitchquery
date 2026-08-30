from fastapi import FastAPI, Depends
from sqlalchemy.engine import Connection
from api.db import get_connection
from api.repositories.base import count_rows, fetch_latest_season
from api.routers import teams, players, fixtures
from api.schemas import HealthOut

app = FastAPI(title="PitchQuery API", version="1.0.0")

# Register routers
app.include_router(teams.router, prefix="/api/v1/teams", tags=["teams"])
app.include_router(players.router, prefix="/api/v1/players", tags=["players"])
app.include_router(fixtures.router, prefix="/api/v1/fixtures", tags=["fixtures"])

@app.get("/health", response_model=HealthOut)
def health(conn: Connection = Depends(get_connection)):
    latest_id, latest_name = fetch_latest_season(conn)
    return HealthOut(
        status="ok",
        db_ready=True,
        latest_season_id=latest_id,
        latest_season_name=latest_name,
        tables={
            t: count_rows(conn, t) 
            for t in ["dim_seasons", "dim_teams", "dim_players", "dim_fixtures"]
        },
    )