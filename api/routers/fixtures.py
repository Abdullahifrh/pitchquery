from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.engine import Connection
from api.db import get_connection
from api.repositories import fixtures 
from api.dependencies import resolve_season_id
from api.schemas import FixtureOut

router = APIRouter()

@router.get("/", response_model=list[FixtureOut])
def list_fixtures(season_id: int | None = Query(None), gameweek: int | None = None, status: str | None = None, conn: Connection = Depends(get_connection)):
    sid = resolve_season_id(conn, season_id)
    return fixtures.fetch_fixtures(conn, sid, gameweek, status)

@router.get("/{fixture_id}", response_model=FixtureOut)
def get_fixture(fixture_id: int, conn: Connection = Depends(get_connection)):
    fix = fixtures.fetch_fixture(conn, fixture_id)
    if not fix: raise HTTPException(404, "Fixture not found")
    return fix

@router.get("/{fixture_id}/events")
def get_fixture_events(fixture_id: int, conn: Connection = Depends(get_connection)):
    return fixtures.fetch_fixture_events(conn, fixture_id)

@router.get("/{fixture_id}/shots")
def get_fixture_shots(fixture_id: int, conn: Connection = Depends(get_connection)):
    return fixtures.fetch_fixture_shots(conn, fixture_id)

@router.get("/{fixture_id}/lineup")
def get_fixture_lineup(fixture_id: int, conn: Connection = Depends(get_connection)):
    return fixtures.fetch_fixture_lineup(conn, fixture_id)