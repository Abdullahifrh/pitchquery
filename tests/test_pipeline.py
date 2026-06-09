import pytest
from pipelines.schema import SCHEMA

CRITICAL_COLUMNS: dict[str, tuple[str, ...]] = {
    "fact_match_lineup": ("player_id", "fixture_id", "season_id", "team_id", "minutes_played", "starter_flag"),
    "fact_player_season_stats": ("player_id", "season_id"),
    "fact_team_match_stats": ("fixture_id", "season_id", "team_id", "is_home"),
    "fact_team_season_stats": ("team_id", "season_id"),
    "fact_premier_league_table": ("team_id", "season_id"),
}

SEASON_SCOPED_TABLES = (
    "dim_seasons",
    "dim_fixtures",
    "bridge_player_seasons",
    "fact_match_lineup",
    "fact_player_season_stats",
    "fact_team_match_stats",
    "fact_team_season_stats",
    "fact_match_events",
    "fact_shot_events",
    "fact_premier_league_table",
)

def expected_columns(table_name: str) -> tuple[str, ...]:
    contract = SCHEMA[table_name]
    if contract.columns is not None:
        return contract.columns
    return CRITICAL_COLUMNS[table_name]

@pytest.mark.parametrize("table_name", list(SCHEMA.keys()))
def test_table_present(frames, table_name, request):
    assert table_name in frames
    df = frames[table_name]
    # Bind volume payload metadata to the running test node
    request.node.data_quality_records = len(df)

@pytest.mark.parametrize("table_name", list(SCHEMA.keys()))
def test_required_columns_exist(frames, table_name):
    df = frames[table_name]
    missing = [col for col in expected_columns(table_name) if col not in df.columns]
    assert not missing, f"{table_name}: missing columns {missing}"

@pytest.mark.parametrize("table_name", list(SCHEMA.keys()))
def test_primary_key_unique(frames, table_name, request):
    df = frames[table_name]
    pk = list(SCHEMA[table_name].primary_key)
    request.node.data_quality_records = len(df)
    
    assert not df.duplicated(subset=pk).any(), f"{table_name}: duplicate primary key rows found"
    assert df[pk].notna().all().all(), f"{table_name}: nulls found in primary key columns"

@pytest.mark.parametrize("table_name", SEASON_SCOPED_TABLES)
def test_season_consistency(frames, season_id, table_name):
    df = frames[table_name]
    assert "season_id" in df.columns
    assert df["season_id"].nunique(dropna=False) == 1
    assert int(df["season_id"].iloc[0]) == season_id

def test_dim_seasons_business_rules(frames):
    df = frames["dim_seasons"]
    assert len(df) == 1
    assert df["competition_name"].eq("Premier League").all()

def test_dim_teams_business_rules(frames):
    df = frames["dim_teams"]
    assert len(df) == 20
    assert df["team_name"].is_unique
    assert df["team_logo_url"].notna().all()

def test_dim_players_business_rules(frames):
    df = frames["dim_players"]
    assert df["player_name"].notna().all()

def test_fact_match_lineup_business_rules(frames):
    df = frames["fact_match_lineup"]
    assert df["minutes_played"].fillna(-1).ge(0).all()
    assert set(df["starter_flag"].dropna().unique()).issubset({0, 1, True, False})

def test_fact_shot_events_business_rules(frames):
    df = frames["fact_shot_events"]
    valid_categories = ["Inside Box", "Outside Box"]
    assert df["distance"].dropna().isin(valid_categories).all(), (
        "Data Quality Failure: Unexpected text label found inside fact_shot_events['distance'] column."
    )

def test_fact_team_match_stats_business_rules(frames):
    df = frames["fact_team_match_stats"]
    assert set(df["is_home"].dropna().unique()).issubset({0, 1, True, False})

def test_fact_premier_league_table_business_rules(frames):
    df = frames["fact_premier_league_table"]
    assert len(df) == 20
    assert df["team_id"].is_unique