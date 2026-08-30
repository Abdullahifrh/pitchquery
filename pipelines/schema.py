from dataclasses import dataclass
from typing import Any
import pandas as pd

SCHEMA_VERSION = "warehouse_v1"
DEFAULT_COMPETITION_NAME = "Premier League"

# ingested_at is excluded from upserts (set once, never overwritten); these
# aren't part of any TableContract's business columns.
AUDIT_COLUMNS: tuple[str, ...] = ("ingested_at", "updated_at")

class SchemaValidationError(ValueError):
    """Raised when a frame doesn't conform to its TableContract before a warehouse load."""

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
        # Composite PK is deliberate: transfer_sequence resets each
        # season, so season_id does real disambiguation here.
        primary_key=("season_id", "bridge_player_season_id"),
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
        # fixture_id-scoped id, globally unique, no season_id needed.
        primary_key=("match_event_id",),
        columns=(
            "match_event_id", "fixture_id", "season_id", "team_id", "event_type",
            # Six explicit role columns, not a positional pair — see
            # classify_match_event_player_roles in transform/fixtures.py.
            "scorer_player_id", "assist_player_id", "own_goal_player_id",
            "carded_player_id", "player_on_id", "player_off_id",
            # minute (int) / minute_display (text) split for numeric SQL predicates.
            "minute", "minute_display", "is_stoppage_time",
        ),
    ),
    "fact_shot_events": TableContract(
        name="fact_shot_events",
        # Same fixture_id-alone uniqueness reasoning as match_event_id above.
        primary_key=("shot_event_id",),
        columns=(
            "shot_event_id",
            "fixture_id",
            "season_id",
            "team_id",
            "player1_id",
            "player2_id",
            "minute", "minute_display", "is_stoppage_time",
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

# Foreign keys — single source of truth, read by
# pipelines/load/warehouse.py::ensure_warehouse_table at table-creation time.
# Dimension tables must exist before a fact table referencing them is first created.
FOREIGN_KEYS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "dim_fixtures": (
        ("season_id", "dim_seasons", "season_id"),
        ("home_team_id", "dim_teams", "team_id"),
        ("away_team_id", "dim_teams", "team_id"),
    ),
    "bridge_player_seasons": (
        ("player_id", "dim_players", "player_id"),
        ("season_id", "dim_seasons", "season_id"),
        ("team_id", "dim_teams", "team_id"),
    ),
    "fact_match_lineup": (
        ("player_id", "dim_players", "player_id"),
        ("fixture_id", "dim_fixtures", "fixture_id"),
        ("team_id", "dim_teams", "team_id"),
    ),
    "fact_player_season_stats": (
        ("player_id", "dim_players", "player_id"),
        ("season_id", "dim_seasons", "season_id"),
    ),
    "fact_team_match_stats": (
        ("fixture_id", "dim_fixtures", "fixture_id"),
        ("team_id", "dim_teams", "team_id"),
        ("season_id", "dim_seasons", "season_id"),
    ),
    "fact_team_season_stats": (
        ("team_id", "dim_teams", "team_id"),
        ("season_id", "dim_seasons", "season_id"),
    ),
    "fact_match_events": (
        ("fixture_id", "dim_fixtures", "fixture_id"),
        ("season_id", "dim_seasons", "season_id"),
        ("team_id", "dim_teams", "team_id"),
        ("scorer_player_id", "dim_players", "player_id"),
        ("assist_player_id", "dim_players", "player_id"),
        ("own_goal_player_id", "dim_players", "player_id"),
        ("carded_player_id", "dim_players", "player_id"),
        ("player_on_id", "dim_players", "player_id"),
        ("player_off_id", "dim_players", "player_id"),
    ),
    "fact_shot_events": (
        ("fixture_id", "dim_fixtures", "fixture_id"),
        ("season_id", "dim_seasons", "season_id"),
        ("team_id", "dim_teams", "team_id"),
        ("player1_id", "dim_players", "player_id"),
        ("player2_id", "dim_players", "player_id"),
    ),
    "fact_premier_league_table": (
        ("team_id", "dim_teams", "team_id"),
        ("season_id", "dim_seasons", "season_id"),
    ),
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

def apply_audit_columns(df: pd.DataFrame, as_of: "pd.Timestamp | None" = None) -> pd.DataFrame:
    """Stamp ingested_at/updated_at onto every row with one consistent timestamp; preserves existing values."""
    out = df.copy()
    stamp = as_of if as_of is not None else pd.Timestamp.utcnow()
    for col in AUDIT_COLUMNS:
        if col not in out.columns:
            out[col] = stamp
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
            raise SchemaValidationError(
                f"{table_name}: renamed target columns already exist in the frame: {sorted(existing_targets)}"
            )
        df = df.rename(columns=contract.rename)

    df = apply_constants(df, contract.constants)

    if contract.columns is not None:
        missing = [col for col in contract.columns if col not in df.columns]
        if missing:
            raise SchemaValidationError(f"{table_name}: missing required columns after materialization: {missing}")
        # Keep existing audit columns so re-materializing doesn't reset ingested_at.
        select_cols = list(contract.columns) + [c for c in AUDIT_COLUMNS if c in df.columns]
        df = df.loc[:, select_cols].copy()

    df = apply_audit_columns(df)

    return df.reset_index(drop=True)

def materialize_frames(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    return {table_name: materialize_table(table_name, frame) for table_name, frame in frames.items()}

def validate_materialized_frame(table_name: str, frame: pd.DataFrame) -> pd.DataFrame:
    """Fail-fast column check for a frame already materialized (e.g. read back from CSV) — never renames/drops."""
    if table_name not in SCHEMA:
        raise KeyError(f"Unknown warehouse table: {table_name}")

    contract = SCHEMA[table_name]
    if contract.columns is not None:
        missing = [col for col in contract.columns if col not in frame.columns]
        if missing:
            raise SchemaValidationError(
                f"{table_name}: missing required columns in already-materialized frame: {missing}"
            )
    return frame

def validate_materialized_frames(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    return {table_name: validate_materialized_frame(table_name, frame) for table_name, frame in frames.items()}

def primary_key_columns(table_name: str) -> tuple[str, ...]:
    if table_name not in SCHEMA:
        raise KeyError(f"Unknown warehouse table: {table_name}")
    return SCHEMA[table_name].primary_key

def snapshot_season_slug(frames: dict[str, pd.DataFrame], season_id: int) -> str:
    """Folder-naming slug for a season, e.g. 'season=2026_27/'; falls back to 'season_<id>' if unparseable."""
    from pipelines.utils import parse_season_slug

    dim_seasons = frames.get("dim_seasons")
    if isinstance(dim_seasons, pd.DataFrame) and not dim_seasons.empty and "season_name" in dim_seasons.columns:
        raw = str(dim_seasons.iloc[0]["season_name"])
        try:
            return parse_season_slug(raw)
        except ValueError:
            print(f"[WARN] snapshot_season_slug: could not parse season label {raw!r}, falling back to season_{season_id}")
    return f"season_{season_id}"
