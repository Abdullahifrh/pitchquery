from dataclasses import dataclass
from typing import Any
import pandas as pd

SCHEMA_VERSION = "warehouse_v1"
DEFAULT_COMPETITION_NAME = "Premier League"


@dataclass(frozen=True)
class TableContract:
    name: str
    primary_key: tuple[str, ...]
    columns: tuple[str, ...] | None = None
    rename: dict[str, str] | None = None
    constants: dict[str, Any] | None = None


SCHEMA: dict[str, TableContract] = {
    "dim_seasons": TableContract(
        name="dim_seasons",
        primary_key=("season_id",),
        columns=("season_id", "season_name", "competition_name"),
        constants={"competition_name": DEFAULT_COMPETITION_NAME},
    ),
    "dim_teams": TableContract(
        name="dim_teams",
        primary_key=("team_id",),
        columns=("team_id", "team_name", "short_name", "team_logo_url"),
    ),
    "dim_players": TableContract(
        name="dim_players",
        primary_key=("player_id",),
        columns=("player_id", "player_name", "date_of_birth", "country", "player_photo_url"),
        rename={"pulse_player_id": "player_id"},
    ),
    "dim_fixtures": TableContract(
        name="dim_fixtures",
        primary_key=("fixture_id",),
        columns=(
            "fixture_id",
            "season_id",
            "gameweek",
            "kickoff_datetime",
            "stadium",
            "attendance",
            "home_team_id",
            "away_team_id",
            "fixture_status",
        ),
    ),
    "bridge_player_seasons": TableContract(
        name="bridge_player_seasons",
        primary_key=("bridge_player_season_id",),
        columns=(
            "bridge_player_season_id",
            "player_id",
            "season_id",
            "team_id",
            "position",
            "position_info",
            "shirt_number",
            "age",
            "transfer_sequence",
        ),
    ),
    "fact_match_lineup": TableContract(name="fact_match_lineup", primary_key=("player_id", "fixture_id")),
    "fact_player_season_stats": TableContract(
        name="fact_player_season_stats",
        primary_key=("player_id", "season_id"),
    ),
    "fact_team_match_stats": TableContract(
        name="fact_team_match_stats",
        primary_key=("fixture_id", "team_id"),
    ),
    "fact_team_season_stats": TableContract(
        name="fact_team_season_stats",
        primary_key=("team_id", "season_id"),
    ),
    "fact_match_events": TableContract(
        name="fact_match_events",
        primary_key=("match_event_id",),
        columns=("match_event_id", "fixture_id", "season_id", "team_id", "event_type", "player1_id", "player2_id", "minute"),
    ),
    "fact_shot_events": TableContract(
        name="fact_shot_events",
        primary_key=("shot_event_id",),
        columns=(
            "shot_event_id",
            "fixture_id",
            "season_id",
            "team_id",
            "player1_id",
            "player2_id",
            "minute",
            "shot_type",
            "body_part",
            "distance",
            "outcome",
        ),
    ),
    "fact_premier_league_table": TableContract(
        name="fact_premier_league_table",
        primary_key=("team_id", "season_id"),
    ),
}

KEEP_ALL_COLUMNS = {
    "fact_match_lineup",
    "fact_player_season_stats",
    "fact_team_match_stats",
    "fact_team_season_stats",
    "fact_premier_league_table",
}


def contract_table_names() -> tuple[str, ...]:
    return tuple(SCHEMA.keys())


def apply_constants(df: pd.DataFrame, constants: dict[str, Any] | None) -> pd.DataFrame:
    out = df.copy()
    if not constants:
        return out

    for col, value in constants.items():
        if col not in out.columns:
            out[col] = value
    return out


def materialize_table(table_name: str, frame: pd.DataFrame) -> pd.DataFrame:
    if table_name not in SCHEMA:
        raise KeyError(f"Unknown warehouse table: {table_name}")

    contract = SCHEMA[table_name]
    df = frame.copy()

    if contract.rename:
        rename_targets = set(contract.rename.values())
        existing_targets = rename_targets & set(df.columns) - set(contract.rename.keys())
        if existing_targets:
            raise ValueError(
                f"{table_name}: renamed target columns already exist in the frame: {sorted(existing_targets)}"
            )
        df = df.rename(columns=contract.rename)

    df = apply_constants(df, contract.constants)

    if contract.columns is not None:
        missing = [col for col in contract.columns if col not in df.columns]
        if missing:
            raise ValueError(f"{table_name}: missing required columns after materialization: {missing}")
        df = df.loc[:, list(contract.columns)].copy()

    return df.reset_index(drop=True)


def materialize_frames(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    return {table_name: materialize_table(table_name, frame) for table_name, frame in frames.items()}


def primary_key_columns(table_name: str) -> tuple[str, ...]:
    if table_name not in SCHEMA:
        raise KeyError(f"Unknown warehouse table: {table_name}")
    return SCHEMA[table_name].primary_key


def required_columns(table_name: str) -> tuple[str, ...] | None:
    if table_name not in SCHEMA:
        raise KeyError(f"Unknown warehouse table: {table_name}")
    return SCHEMA[table_name].columns


def snapshot_season_slug(frames: dict[str, pd.DataFrame], season_id: int) -> str:
    dim_seasons = frames.get("dim_seasons")
    if isinstance(dim_seasons, pd.DataFrame) and not dim_seasons.empty and "season_name" in dim_seasons.columns:
        raw = str(dim_seasons.iloc[0]["season_name"])
        slug = raw.strip().replace("/", "_").replace(" ", "_")
        slug = "".join(ch for ch in slug if ch.isalnum() or ch == "_")
        return slug or f"season_{season_id}"
    return f"season_{season_id}"
