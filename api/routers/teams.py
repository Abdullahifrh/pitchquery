from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.engine import Connection
from api.db import get_connection
from api.repositories import teams
from api.dependencies import resolve_season_id
from api.schemas import TeamOut, PlayerOut

router = APIRouter()

@router.get("/", response_model=list[TeamOut])
def list_teams(conn: Connection = Depends(get_connection)):
    return teams.fetch_teams(conn)

@router.get("/standings")
def get_standings(season_id: int | None = Query(None), conn: Connection = Depends(get_connection)):
    sid = resolve_season_id(conn, season_id)
    return teams.fetch_standings(conn, sid)

@router.get("/{team_id}", response_model=TeamOut)
def get_team(team_id: int, conn: Connection = Depends(get_connection)):
    team = teams.fetch_team(conn, team_id)
    if not team: raise HTTPException(404, "Team not found")
    return team

@router.get("/{team_id}/players", response_model=list[PlayerOut])
def get_team_players(team_id: int, season_id: int | None = Query(None), conn: Connection = Depends(get_connection)):
    sid = resolve_season_id(conn, season_id)
    return teams.fetch_team_players(conn, team_id, sid)

@router.get("/{team_id}/season-stats")
def get_team_season_stats(team_id: int, season_id: int | None = Query(None), conn: Connection = Depends(get_connection)):
    sid = resolve_season_id(conn, season_id)
    stats = teams.fetch_team_season_stats(conn, team_id, sid)
    if not stats: raise HTTPException(404, "Stats not found")
    return stats

@router.get("/{team_id}/match-stats")
def get_team_match_stats(
    team_id: int,
    season_id: int | None = Query(None),
    conn: Connection = Depends(get_connection),
):
    sid = resolve_season_id(conn, season_id)
    return teams.fetch_team_match_stats(conn, team_id, sid)