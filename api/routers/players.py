from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.engine import Connection
from api.db import get_connection
from api.repositories import players
from api.dependencies import resolve_season_id
from api.schemas import PlayerOut

router = APIRouter()

@router.get("/", response_model=list[PlayerOut])
def list_players(conn: Connection = Depends(get_connection)):
    return players.fetch_players(conn)

@router.get("/{player_id}", response_model=PlayerOut)
def get_player(player_id: int, conn: Connection = Depends(get_connection)):
    player = players.fetch_player(conn, player_id)
    if not player: raise HTTPException(404, "Player not found")
    return player

@router.get("/{player_id}/seasons")
def get_player_seasons(player_id: int, conn: Connection = Depends(get_connection)):
    return players.fetch_player_seasons(conn, player_id)

@router.get("/{player_id}/season-stats")
def get_player_season_stats(player_id: int, season_id: int | None = Query(None), conn: Connection = Depends(get_connection)):
    sid = resolve_season_id(conn, season_id)
    stats = players.fetch_player_season_stats(conn, player_id, sid)
    if not stats: raise HTTPException(404, "Stats not found")
    return stats