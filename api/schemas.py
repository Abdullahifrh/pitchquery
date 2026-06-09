from datetime import date, datetime
from pydantic import BaseModel

class HealthOut(BaseModel):
    status: str
    db_ready: bool
    latest_season_id: int | None = None
    latest_season_name: str | None = None
    tables: dict[str, int]


class TeamOut(BaseModel):
    team_id: int
    team_name: str | None = None
    short_name: str | None = None
    team_logo_url: str | None = None


class PlayerOut(BaseModel):
    player_id: int
    player_name: str | None = None
    date_of_birth: date | None = None
    country: str | None = None
    player_photo_url: str | None = None


class FixtureOut(BaseModel):
    fixture_id: int
    season_id: int
    gameweek: int | None = None
    kickoff_datetime: datetime | None = None
    stadium: str | None = None
    attendance: int | None = None
    home_team_id: int | None = None
    away_team_id: int | None = None
    fixture_status: str | None = None