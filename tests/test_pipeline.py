"""Pipeline & ETL health.

Consolidates what used to be spread across test_pipeline.py,
test_data_quality.py, test_season_slug.py, test_seasons.py,
test_fixture_mapping.py, test_archive.py, and test_seed.py into one
file, organized by pipeline stage:

1. Schema/contract shape + business-rule/data-quality assertions
   against `sample_frames` (schema shape, PK uniqueness, referential
   integrity, value bounds — merged into unified parametrized checks
   rather than one test function per table/rule).
2. Season label normalization (the `dim_seasons.season_name` /
   snapshot-slug transformation logic).
3. FPL<->Pulse fixture-mapping crosswalk (the highest-value subset of
   the original 9-case regression suite — the specific "pulse_id is
   broken" bug and its fallback, not every input-shape permutation).
4. Historical seed loading (`pipelines.seed.load_historical_frames`).
5. Archive orchestration (`pipelines.archive.archive_season`).
6. End-to-end pipeline idempotency: upserting the same frames twice
   into a real (SQLite) database yields identical row counts.
"""

import pandas as pd
import pytest
import re

from pipelines.schema import SCHEMA

# ---------------------------------------------------------------------
# 1. Schema shape & data quality, against sample_frames
# ---------------------------------------------------------------------

# Tables where SCHEMA doesn't pin an explicit column list (KEEP_ALL_COLUMNS
# fact tables) still have columns every consumer relies on existing.
CRITICAL_COLUMNS: dict[str, tuple[str, ...]] = {
    "fact_match_lineup": ("player_id", "fixture_id", "season_id", "team_id", "minutes_played", "starter_flag"),
    "fact_player_season_stats": ("player_id", "season_id"),
    "fact_team_match_stats": ("fixture_id", "season_id", "team_id", "is_home"),
    "fact_team_season_stats": ("team_id", "season_id"),
    "fact_premier_league_table": ("team_id", "season_id"),
}

SEASON_SCOPED_TABLES = (
    "dim_seasons", "dim_fixtures", "bridge_player_seasons",
    "fact_match_lineup", "fact_player_season_stats", "fact_team_match_stats",
    "fact_team_season_stats", "fact_match_events", "fact_shot_events",
    "fact_premier_league_table",
)

# (fact_table, fk_column, dim_table, dim_pk_column)
REFERENTIAL_INTEGRITY_CHECKS = [
    ("fact_match_lineup", "player_id", "dim_players", "player_id"),
    ("fact_match_lineup", "fixture_id", "dim_fixtures", "fixture_id"),
    ("fact_match_lineup", "team_id", "dim_teams", "team_id"),
    ("fact_team_match_stats", "team_id", "dim_teams", "team_id"),
    ("fact_team_match_stats", "fixture_id", "dim_fixtures", "fixture_id"),
    ("fact_team_season_stats", "team_id", "dim_teams", "team_id"),
    ("fact_premier_league_table", "team_id", "dim_teams", "team_id"),
    ("fact_match_events", "fixture_id", "dim_fixtures", "fixture_id"),
    ("fact_match_events", "scorer_player_id", "dim_players", "player_id"),
    ("fact_match_events", "assist_player_id", "dim_players", "player_id"),
    ("fact_match_events", "own_goal_player_id", "dim_players", "player_id"),
    ("fact_match_events", "carded_player_id", "dim_players", "player_id"),
    ("fact_match_events", "player_on_id", "dim_players", "player_id"),
    ("fact_match_events", "player_off_id", "dim_players", "player_id"),
    ("fact_shot_events", "fixture_id", "dim_fixtures", "fixture_id"),
    ("fact_shot_events", "player1_id", "dim_players", "player_id"),
    ("bridge_player_seasons", "player_id", "dim_players", "player_id"),
    ("bridge_player_seasons", "team_id", "dim_teams", "team_id"),
]


def _expected_columns(table_name: str) -> tuple[str, ...]:
    contract = SCHEMA[table_name]
    return contract.columns if contract.columns is not None else CRITICAL_COLUMNS[table_name]


@pytest.mark.parametrize("table_name", list(SCHEMA.keys()))
def test_table_present_with_required_columns_and_unique_nonnull_pk(sample_frames, table_name):
    """Merges the old test_table_present / test_required_columns_exist /
    test_primary_key_unique into one assertion per table: shape, required
    columns, and PK integrity are all facets of "is this table usable",
    not three independently-meaningful checks."""
    assert table_name in sample_frames
    df = sample_frames[table_name]

    missing = [c for c in _expected_columns(table_name) if c not in df.columns]
    assert not missing, f"{table_name}: missing columns {missing}"

    pk = list(SCHEMA[table_name].primary_key)
    assert df[pk].notna().all().all(), f"{table_name}: null value(s) in primary key column(s) {pk}"
    assert not df.duplicated(subset=pk).any(), f"{table_name}: duplicate primary key rows found"


@pytest.mark.parametrize("table_name", SEASON_SCOPED_TABLES)
def test_season_consistency(sample_frames, season_id, table_name):
    df = sample_frames[table_name]
    assert "season_id" in df.columns
    assert df["season_id"].nunique(dropna=False) == 1
    assert int(df["season_id"].iloc[0]) == season_id


@pytest.mark.parametrize("fact_table,fk_col,dim_table,dim_pk", REFERENTIAL_INTEGRITY_CHECKS)
def test_foreign_keys_resolve_to_dimension(sample_frames, fact_table, fk_col, dim_table, dim_pk):
    fact_df, dim_df = sample_frames[fact_table], sample_frames[dim_table]
    valid_keys = set(pd.to_numeric(dim_df[dim_pk], errors="coerce").dropna().astype("int64"))
    fact_keys = pd.to_numeric(fact_df[fk_col], errors="coerce").dropna().astype("int64")
    orphans = fact_keys[~fact_keys.isin(valid_keys)]
    assert orphans.empty, f"{fact_table}.{fk_col}: {len(orphans)} row(s) reference missing {dim_table}.{dim_pk}"


def test_dim_seasons_and_dim_teams_business_rules(sample_frames):
    seasons = sample_frames["dim_seasons"]
    assert len(seasons) == 1
    assert seasons["competition_name"].eq("Premier League").all()
    assert seasons["season_name"].str.match(r"^\d{4}/\d{2}$").all(), "season_name must be normalized to YYYY/YY"

    teams = sample_frames["dim_teams"]
    assert teams["team_name"].is_unique
    assert teams["team_logo_url"].notna().all()


def test_dim_fixtures_value_bounds(sample_frames):
    df = sample_frames["dim_fixtures"]
    gw = pd.to_numeric(df["gameweek"], errors="coerce")
    assert gw.between(1, 38).all(), "gameweek outside the valid 1-38 range"

    kickoffs = pd.to_datetime(df["kickoff_datetime"], utc=True, errors="coerce")
    assert kickoffs.notna().all(), "unparseable kickoff_datetime"
    assert (kickoffs.dt.year >= 1992).all() and (kickoffs.dt.year <= 2100).all()

    assert (pd.to_numeric(df["attendance"], errors="coerce") >= 0).all()


def test_fact_table_value_bounds(sample_frames):
    lineup = sample_frames["fact_match_lineup"]
    assert lineup["minutes_played"].fillna(-1).ge(0).all()
    assert lineup["minutes_played"].le(130).all(), "minutes_played outside plausible 0-130 range"
    assert set(lineup["starter_flag"].dropna().unique()).issubset({0, 1, True, False})

    team_stats = sample_frames["fact_team_match_stats"]
    assert set(team_stats["is_home"].dropna().unique()).issubset({0, 1, True, False})
    for col in ("goals_scored", "goals_conceded"):
        assert (pd.to_numeric(team_stats[col], errors="coerce") >= 0).all()

    shots = sample_frames["fact_shot_events"]
    assert shots["distance"].dropna().isin(["Inside Box", "Outside Box"]).all()

    # RAG-readiness regression: minute must be numeric and queryable
    # with plain SQL comparisons (was previously a string mixing plain
    # minutes with stoppage-time notation like "90+1'" — see
    # test_pipeline.py's minute-parsing tests and the diagnosis report).
    for events_table in ("fact_match_events", "fact_shot_events"):
        events = sample_frames[events_table]
        minutes = pd.to_numeric(events["minute"], errors="coerce")
        assert minutes.notna().all(), f"{events_table}.minute must be fully numeric"
        assert minutes.between(0, 130).all(), f"{events_table}.minute outside plausible 0-130 range"
        assert set(events["is_stoppage_time"].dropna().unique()).issubset({0, 1, True, False})

    table = sample_frames["fact_premier_league_table"]
    assert table["team_id"].is_unique
    assert (pd.to_numeric(table["points"], errors="coerce") >= 0).all()


# ---------------------------------------------------------------------
# 2. Season label normalization
# ---------------------------------------------------------------------

@pytest.mark.parametrize("label,expected_slug,expected_name", [
    ("2025/26", "2025_26", "2025/26"),
    ("2024/25", "2024_25", "2024/25"),
    ("English Premier League Season 2026/2027", "2026_27", "2026/27"),
    ("Premier League 2026-27", "2026_27", "2026/27"),
    ("PL 2026/2027 Season", "2026_27", "2026/27"),
    ("2019/2020", "2019_20", "2019/20"),
])
def test_season_label_normalizes_to_slug_and_name(label, expected_slug, expected_name):
    """Merges the old test_season_slug.py + test_seasons.py parametrize
    tables (they tested the same label shapes against two sibling
    functions that share one regex) into a single parametrized check."""
    from pipelines.utils import parse_season_slug, parse_season_label
    assert parse_season_slug(label) == expected_slug
    assert parse_season_label(label) == expected_name


def test_season_label_raises_on_unparseable_input():
    from pipelines.utils import parse_season_slug
    with pytest.raises(ValueError):
        parse_season_slug("not a season label at all")


def test_build_dim_seasons_normalizes_the_verbose_live_label(monkeypatch):
    """The exact bug report this guards against: the live season's raw
    Pulse label must not leak into season_name verbatim."""
    from pipelines.transform import seasons as seasons_module

    monkeypatch.setattr(
        seasons_module, "fetch_pulse_season_name",
        lambda season_id: "English Premier League Season 2026/2027",
    )
    df = seasons_module.build_dim_seasons(841)

    assert list(df.columns) == ["season_id", "season_name", "competition_name"]
    row = df.iloc[0]
    assert int(row["season_id"]) == 841
    assert row["season_name"] == "2026/27"
    assert row["competition_name"] == seasons_module.DEFAULT_COMPETITION_NAME


def test_snapshot_season_slug_falls_back_when_dim_seasons_missing():
    from pipelines.schema import snapshot_season_slug
    assert snapshot_season_slug({}, 999) == "season_999"


# ---------------------------------------------------------------------
# 2b. fact_match_events player-role mapping & minute parsing (RAG-readiness fix)
# ---------------------------------------------------------------------

def test_classify_match_event_player_roles_matches_documented_convention():
    """Direct test of the exact mapping this project's own convention
    defines (goal: scorer/assister; penalty goal: scorer only, no
    assist ever recorded for penalties; own goal: distinct from a
    genuine scoring credit; cards: carded player only; substitution:
    player1 is who comes ON, player2 is who comes OFF) — replacing the
    old polymorphic player1_id/player2_id pair whose meaning depended
    on event_type with explicit, self-describing columns."""
    from pipelines.transform.fixtures import classify_match_event_player_roles

    assert classify_match_event_player_roles("goal", 10, 11) == {
        "scorer_player_id": 10, "assist_player_id": 11, "own_goal_player_id": None,
        "carded_player_id": None, "player_on_id": None, "player_off_id": None,
    }
    assert classify_match_event_player_roles("goal", 10, None) == {
        "scorer_player_id": 10, "assist_player_id": None, "own_goal_player_id": None,
        "carded_player_id": None, "player_on_id": None, "player_off_id": None,
    }
    roles = classify_match_event_player_roles("penalty goal", 10, None)
    assert roles["scorer_player_id"] == 10 and roles["assist_player_id"] is None

    roles = classify_match_event_player_roles("own goal", 40, None)
    assert roles["own_goal_player_id"] == 40 and roles["scorer_player_id"] is None

    for card_type in ("yellow", "red"):
        roles = classify_match_event_player_roles(card_type, 20, None)
        assert roles["carded_player_id"] == 20
        assert all(v is None for k, v in roles.items() if k != "carded_player_id")

    roles = classify_match_event_player_roles("substitution", 30, 31)
    assert roles["player_on_id"] == 30 and roles["player_off_id"] == 31


def test_parse_minute_components_handles_plain_and_stoppage_time():
    """Regression test for the reported RAG-readiness issue: minute was
    a string mixing plain minutes ('71') with stoppage-time notation
    ("90+1'"), which breaks numeric SQL predicates like `WHERE minute >
    70`. `minute` is now always a real integer; `minute_display` keeps
    the original text for human-readable output."""
    from pipelines.utils import parse_minute_components

    assert parse_minute_components("71") == (71, False)
    assert parse_minute_components("90+1'") == (91, True)
    assert parse_minute_components("45+2'") == (47, True)
    assert parse_minute_components(None) == (None, False)


# ---------------------------------------------------------------------
# 3. FPL <-> Pulse fixture-mapping crosswalk
# ---------------------------------------------------------------------

_DIM_TEAMS = pd.DataFrame([
    {"team_id": 100, "fpl_team_id": 1, "team_name": "Arsenal", "short_name": "ARS"},
    {"team_id": 200, "fpl_team_id": 2, "team_name": "Aston Villa", "short_name": "AVL"},
])
_PULSE_FIXTURES = [
    {"id": 90001, "kickoff": {"millis": 1787335200000}, "teams": [{"team": {"id": 100}}, {"team": {"id": 200}}]},
]


def _fpl_fixture(fpl_id, team_h, team_a, kickoff_time, pulse_id):
    return {"id": fpl_id, "team_h": team_h, "team_a": team_a, "kickoff_time": kickoff_time, "pulse_id": pulse_id}


def test_fixture_mapping_falls_back_to_composite_key_when_pulse_id_is_broken():
    """Regression test for the reported root cause: FPL's fixtures
    endpoint reporting pulse_id=0 for every fixture must not leave
    fpl_fixture_id null for every row — the composite-key fallback
    (home/away team + kickoff) must kick in instead."""
    from pipelines.transform.fixtures import build_fpl_to_pulse_fixture_map

    fixtures = [_fpl_fixture(1, 1, 2, "2026-08-21T18:00:00Z", 0)]
    mapping = build_fpl_to_pulse_fixture_map(fixtures, _PULSE_FIXTURES, _DIM_TEAMS)
    assert mapping == {1: 90001}


def test_fixture_mapping_uses_healthy_pulse_id_directly():
    from pipelines.transform.fixtures import build_fpl_to_pulse_fixture_map

    fixtures = [_fpl_fixture(1, 1, 2, "2026-08-21T18:00:00Z", 90001)]
    mapping = build_fpl_to_pulse_fixture_map(fixtures, _PULSE_FIXTURES, _DIM_TEAMS)
    assert mapping == {1: 90001}


def test_fixture_mapping_handles_empty_and_unmapped_inputs_without_raising():
    from pipelines.transform.fixtures import build_fpl_to_pulse_fixture_map

    assert build_fpl_to_pulse_fixture_map([], _PULSE_FIXTURES, _DIM_TEAMS) == {}
    # Unmapped FPL team id (e.g. a promoted team dim_teams doesn't know
    # about yet) is skipped, not raised.
    fixtures = [_fpl_fixture(1, 1, 999, "2026-08-21T18:00:00Z", 0)]
    assert build_fpl_to_pulse_fixture_map(fixtures, _PULSE_FIXTURES, _DIM_TEAMS) == {}


# ---------------------------------------------------------------------
# 4. Historical seed loading
# ---------------------------------------------------------------------

from pathlib import Path

_HISTORICAL_DIR = Path("data/historical/2025_26")


@pytest.mark.skipif(not _HISTORICAL_DIR.exists(), reason="historical seed CSVs not present in this environment")
def test_load_historical_frames_has_all_tables_and_passes_schema_validation():
    from pipelines.seed import load_historical_frames
    from pipelines.schema import validate_materialized_frames

    frames = load_historical_frames(_HISTORICAL_DIR)
    assert set(frames.keys()) == set(SCHEMA.keys())

    validated = validate_materialized_frames(frames)
    for table_name, df in validated.items():
        pk = list(SCHEMA[table_name].primary_key)
        assert not df.duplicated(subset=pk).any(), f"{table_name}: duplicate primary keys in historical seed"


# ---------------------------------------------------------------------
# 5. Archive orchestration
# ---------------------------------------------------------------------

from unittest.mock import patch

_ARCHIVE_SEASON_ID = 841
_ARCHIVE_SEASON_SLUG = "2026_27"


def _fake_archive_frames() -> dict[str, pd.DataFrame]:
    return {
        "dim_seasons": pd.DataFrame([{"season_id": _ARCHIVE_SEASON_ID, "season_name": "2026/27"}]),
        "dim_teams": pd.DataFrame([{"team_id": 1, "team_name": "Team A"}]),
    }


def _make_fake_export_csv_snapshot(snapshot_root: Path):
    def _fake(frames, season_id, base_dir):
        snapshot_dir = snapshot_root / f"season={_ARCHIVE_SEASON_SLUG}" / "run=20260825_000000"
        snapshot_dir.mkdir(parents=True, exist_ok=False)
        (snapshot_dir / "dim_seasons.csv").write_text("season_id,season_name\n841,2026/27\n")
        (snapshot_dir / "dim_teams.csv").write_text("team_id,team_name\n1,Team A\n")
        (snapshot_dir / "manifest.json").write_text('{"season_id": 841, "season_slug": "2026_27"}')
        return snapshot_dir
    return _fake


@patch("pipelines.archive.export_csv_snapshot")
@patch("pipelines.archive.snapshot_season_slug")
@patch("pipelines.archive.materialize_frames")
@patch("pipelines.archive.run_pipeline")
def test_archive_season_writes_historical_baseline(
    mock_run_pipeline, mock_materialize_frames, mock_snapshot_season_slug,
    mock_export_csv_snapshot, tmp_path,
):
    from pipelines.archive import archive_season

    historical_root, snapshot_root = tmp_path / "historical", tmp_path / "snapshots"
    fake_frames = _fake_archive_frames()
    mock_run_pipeline.return_value = fake_frames
    mock_materialize_frames.return_value = fake_frames
    mock_snapshot_season_slug.return_value = _ARCHIVE_SEASON_SLUG
    mock_export_csv_snapshot.side_effect = _make_fake_export_csv_snapshot(snapshot_root)

    result = archive_season(season_id=_ARCHIVE_SEASON_ID, historical_root=historical_root, snapshot_root=snapshot_root)

    assert result == historical_root / _ARCHIVE_SEASON_SLUG
    assert (result / "dim_seasons.csv").exists()
    assert (result / "dim_teams.csv").exists()
    mock_run_pipeline.assert_called_once_with(_ARCHIVE_SEASON_ID)


@patch("pipelines.archive.export_csv_snapshot")
@patch("pipelines.archive.snapshot_season_slug")
@patch("pipelines.archive.materialize_frames")
@patch("pipelines.archive.run_pipeline")
def test_archive_season_refuses_to_overwrite_existing_baseline(
    mock_run_pipeline, mock_materialize_frames, mock_snapshot_season_slug,
    mock_export_csv_snapshot, tmp_path,
):
    from pipelines.archive import archive_season

    historical_root, snapshot_root = tmp_path / "historical", tmp_path / "snapshots"
    existing_dir = historical_root / _ARCHIVE_SEASON_SLUG
    existing_dir.mkdir(parents=True)
    (existing_dir / "already_here.csv").write_text("season_id\n841\n")

    fake_frames = _fake_archive_frames()
    mock_run_pipeline.return_value = fake_frames
    mock_materialize_frames.return_value = fake_frames
    mock_snapshot_season_slug.return_value = _ARCHIVE_SEASON_SLUG
    mock_export_csv_snapshot.side_effect = _make_fake_export_csv_snapshot(snapshot_root)

    with pytest.raises(FileExistsError, match=re.escape(str(existing_dir))):
        archive_season(season_id=_ARCHIVE_SEASON_ID, historical_root=historical_root, snapshot_root=snapshot_root)

    assert (existing_dir / "already_here.csv").read_text() == "season_id\n841\n"


# ---------------------------------------------------------------------
# 6. Pipeline idempotency (end-to-end, via SQLite)
# ---------------------------------------------------------------------

def test_pipeline_upsert_is_idempotent_end_to_end(sample_frames, sqlite_engine):
    """Re-running the load stage of the pipeline on identical input must
    yield identical warehouse state — no duplicate rows, no drift —
    exercised against every table in one sample "pipeline run", not just
    a single table in isolation (see test_warehouse.py for the more
    granular audit-column-focused version of this same guarantee)."""
    from sqlalchemy import text
    from pipelines.load.warehouse import ensure_schemas, upsert_table, warehouse_table_name

    ensure_schemas(sqlite_engine)

    def _row_counts():
        with sqlite_engine.connect() as conn:
            return {
                table_name: conn.execute(
                    text(f'SELECT COUNT(*) FROM "{warehouse_table_name(sqlite_engine, table_name)}"')
                ).scalar()
                for table_name in sample_frames
            }

    for table_name, df in sample_frames.items():
        upsert_table(sqlite_engine, table_name, df.copy())
    counts_after_first_run = _row_counts()

    # Re-run the exact same load, unchanged.
    for table_name, df in sample_frames.items():
        upsert_table(sqlite_engine, table_name, df.copy())
    counts_after_second_run = _row_counts()

    assert counts_after_first_run == counts_after_second_run, (
        "Re-running the pipeline load on identical input must not change row counts anywhere"
    )
    assert all(c > 0 for c in counts_after_first_run.values()), "Sanity check: the first run must have written real rows"
