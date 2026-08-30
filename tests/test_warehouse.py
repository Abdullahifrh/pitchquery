"""Database & warehouse integrity.

Consolidates test_warehouse.py (DatatypeMismatch regression), the DB
layers of test_audit_columns.py + test_audit_harmonization.py, and
test_idempotency.py into one file, organized by the three layers real
warehouse-pipeline DB testing actually covers:

1. Connectivity & Health — the SQLite engine connects, and a session's
   commit/rollback behaves correctly.
2. Schema Integrity & DDL — `ensure_warehouse_table`'s branching logic
   (the historical DatatypeMismatch regression), and that every table's
   DDL includes `ingested_at`/`updated_at` with a `CURRENT_TIMESTAMP`
   default.
3. CRUD & Lineage Operations — `apply_audit_columns` injecting missing
   audit columns into a raw seed DataFrame, and the full upsert
   round-trip against real SQLite: row counts after insertion, ON
   CONFLICT DO UPDATE behavior, and `ingested_at` staying fixed while
   `updated_at` refreshes.

Everything here either needs no I/O at all (pure pandas/mock-based) or
runs against the function-scoped in-memory `sqlite_engine` fixture from
conftest.py — no live Postgres required, so this file runs unattended.
"""

import time
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from pipelines.schema import AUDIT_COLUMNS, SchemaValidationError, apply_audit_columns


# ---------------------------------------------------------------------
# 1. Connectivity & Health
# ---------------------------------------------------------------------

def test_engine_connects_and_executes(sqlite_engine):
    from sqlalchemy import text
    with sqlite_engine.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1


def test_session_commit_persists_and_rollback_discards(sqlite_engine):
    from sqlalchemy import text

    with sqlite_engine.begin() as conn:
        conn.execute(text("CREATE TABLE tx_probe (id INTEGER PRIMARY KEY, value TEXT)"))

    # Committed transaction: value must persist after the block exits.
    with sqlite_engine.begin() as conn:
        conn.execute(text("INSERT INTO tx_probe (id, value) VALUES (1, 'committed')"))
    with sqlite_engine.connect() as conn:
        assert conn.execute(text("SELECT value FROM tx_probe WHERE id = 1")).scalar() == "committed"

    # Rolled-back transaction: value must NOT persist.
    try:
        with sqlite_engine.begin() as conn:
            conn.execute(text("INSERT INTO tx_probe (id, value) VALUES (2, 'rolled_back')"))
            raise RuntimeError("force rollback")
    except RuntimeError:
        pass
    with sqlite_engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM tx_probe WHERE id = 2")).scalar() == 0


# ---------------------------------------------------------------------
# 2. Schema Integrity & DDL
# ---------------------------------------------------------------------

def _empty_lineup_frame() -> pd.DataFrame:
    """Reproduces the shape build_fact_match_lineup() returns when its
    inputs are empty: a 0-row DataFrame with only column headers — the
    exact shape that triggered the original DatatypeMismatch bug."""
    return pd.DataFrame(columns=["player_id", "fixture_id", "season_id", "team_id", "minutes_played", "starter_flag"])


def _nonempty_lineup_frame() -> pd.DataFrame:
    return pd.DataFrame([
        {"player_id": 101, "fixture_id": 5, "season_id": 841, "team_id": 1, "minutes_played": 90, "starter_flag": True},
    ])


class TestEnsureWarehouseTableDatatypeMismatchRegression:
    """Root cause: `ensure_warehouse_table` used to build its in-memory
    `Table`'s column types from *this batch's* DataFrame dtypes on every
    call, even when the real table already existed. An empty batch made
    every pandas column dtype `object` -> every column mapped to
    `String` -> `upsert_table` used that all-String Table as the CAST
    target against the real table's actual INTEGER/BOOLEAN columns ->
    `psycopg2.errors.DatatypeMismatch`. Mocking `sqlalchemy.inspect`/
    `Table` keeps this fast and DB-independent.
    """

    @patch("pipelines.load.warehouse.Table")
    @patch("pipelines.load.warehouse.sa_inspect")
    def test_existing_table_is_always_reflected_never_reinferred(self, mock_sa_inspect, mock_table):
        from pipelines.load.warehouse import ensure_warehouse_table, AUDIT_COLUMNS

        mock_inspector = MagicMock()
        mock_inspector.has_table.return_value = True
        mock_sa_inspect.return_value = mock_inspector
        reflected_table = MagicMock(name="reflected_table")
        # Already has both audit columns, so the retrofit step (see
        # TestAuditColumnRetrofit below) has nothing to do here — this
        # test is purely about "reflect, don't re-infer from the batch".
        reflected_table.columns = [MagicMock(name=c) for c in
                                    ("player_id", "fixture_id", "season_id", "team_id", "minutes_played", "starter_flag", *AUDIT_COLUMNS)]
        for col_mock, name in zip(reflected_table.columns, ("player_id", "fixture_id", "season_id", "team_id", "minutes_played", "starter_flag", *AUDIT_COLUMNS)):
            col_mock.name = name
        mock_table.return_value = reflected_table

        engine = MagicMock()
        for df in (_empty_lineup_frame(), _nonempty_lineup_frame()):
            result = ensure_warehouse_table(engine, "fact_match_lineup", df, pk_cols=("player_id", "fixture_id"))
            _, kwargs = mock_table.call_args
            assert kwargs.get("autoload_with") is engine, "must reflect, never re-infer types from the batch"
            assert result is reflected_table
            engine.begin.assert_not_called()  # no ALTER TABLE — nothing was missing

    @patch("pipelines.load.warehouse.sa_inspect")
    def test_raises_on_first_time_empty_batch(self, mock_sa_inspect):
        from pipelines.load.warehouse import ensure_warehouse_table

        mock_inspector = MagicMock()
        mock_inspector.has_table.return_value = False
        mock_sa_inspect.return_value = mock_inspector

        with pytest.raises(SchemaValidationError, match="fact_match_lineup"):
            ensure_warehouse_table(MagicMock(), "fact_match_lineup", _empty_lineup_frame(), pk_cols=("player_id", "fixture_id"))

    @patch("pipelines.load.warehouse.Table")
    @patch("pipelines.load.warehouse.sa_inspect")
    def test_first_time_nonempty_batch_creates_from_inferred_types(self, mock_sa_inspect, mock_table):
        from pipelines.load.warehouse import ensure_warehouse_table

        mock_inspector = MagicMock()
        mock_inspector.has_table.return_value = False
        mock_sa_inspect.return_value = mock_inspector
        created_table = MagicMock(name="created_table")
        mock_table.return_value = created_table

        result = ensure_warehouse_table(MagicMock(), "fact_match_lineup", _nonempty_lineup_frame(), pk_cols=("player_id", "fixture_id"))

        args, kwargs = mock_table.call_args
        assert "autoload_with" not in kwargs
        created_table.create.assert_called_once()
        assert result is created_table

    @patch("pipelines.load.warehouse.ensure_warehouse_table")
    def test_upsert_table_skips_staging_for_empty_dataframe(self, mock_ensure_warehouse_table):
        from pipelines.load.warehouse import upsert_table

        mock_ensure_warehouse_table.return_value = MagicMock(name="warehouse_table")
        engine = MagicMock()

        upsert_table(engine, "fact_match_lineup", _empty_lineup_frame())

        mock_ensure_warehouse_table.assert_called_once()
        engine.begin.assert_not_called()


class TestAuditColumnDDL:
    """Every warehouse table's DDL must define ingested_at/updated_at
    with server_default=CURRENT_TIMESTAMP, regardless of whether the
    batch that happens to trigger table creation carries real values for
    them or is missing them entirely — a table's schema is a property of
    the table, not of any one batch."""

    @patch("pipelines.load.warehouse.Table")
    @patch("pipelines.load.warehouse.sa_inspect")
    def test_audit_columns_always_present_with_current_timestamp_default(self, mock_sa_inspect, mock_table):
        from pipelines.load.warehouse import ensure_warehouse_table

        mock_inspector = MagicMock()
        mock_inspector.has_table.return_value = False
        mock_sa_inspect.return_value = mock_inspector
        mock_table.return_value = MagicMock(name="created_table")

        # Case A: batch already carries audit columns.
        df_with_audit = pd.DataFrame([{"team_id": 1, "team_name": "X", "short_name": "X", "team_logo_url": "u"}])
        df_with_audit["ingested_at"] = pd.Timestamp.now("UTC")
        df_with_audit["updated_at"] = pd.Timestamp.now("UTC")
        ensure_warehouse_table(MagicMock(), "dim_teams", df_with_audit, pk_cols=("team_id",))
        args, _ = mock_table.call_args
        columns_by_name = {c.name: c for c in args[2:]}
        for col in AUDIT_COLUMNS:
            assert col in columns_by_name
            assert str(columns_by_name[col].server_default.arg) == "CURRENT_TIMESTAMP"

        # Case B: batch is missing audit columns entirely — DDL must still include them.
        df_without_audit = pd.DataFrame([{"team_id": 1, "team_name": "X", "short_name": "X", "team_logo_url": "u"}])
        ensure_warehouse_table(MagicMock(), "dim_teams", df_without_audit, pk_cols=("team_id",))
        args, _ = mock_table.call_args
        column_names = {c.name for c in args[2:]}
        assert AUDIT_COLUMNS[0] in column_names and AUDIT_COLUMNS[1] in column_names


class TestAuditColumnRetrofit:
    """A table created by an earlier version of this pipeline (before
    `schema.AUDIT_COLUMNS` existed, or before a given column was added
    to it) won't have ingested_at/updated_at — `ensure_warehouse_table`
    used to only add them at table-*creation* time, never touching an
    already-existing table. This is why they can appear "missing" for
    tables first created a while ago even though the code has supported
    them all along. See `_retrofit_missing_audit_columns`.
    """

    @patch("pipelines.load.warehouse.Table")
    @patch("pipelines.load.warehouse.sa_inspect")
    def test_missing_audit_columns_trigger_alter_table_and_are_added(self, mock_sa_inspect, mock_table):
        from pipelines.load.warehouse import ensure_warehouse_table

        mock_inspector = MagicMock()
        mock_inspector.has_table.return_value = True
        mock_sa_inspect.return_value = mock_inspector

        # First reflection: table exists, but predates the audit columns.
        stale_table = MagicMock(name="stale_table")
        stale_col = MagicMock(); stale_col.name = "team_id"
        stale_table.columns = [stale_col]

        # Second reflection (after the ALTER TABLEs): now has them.
        migrated_table = MagicMock(name="migrated_table")
        mock_table.side_effect = [stale_table, migrated_table]

        engine = MagicMock()
        df = pd.DataFrame([{"team_id": 1, "team_name": "X", "short_name": "X", "team_logo_url": "u"}])
        df["ingested_at"] = pd.Timestamp.now("UTC")
        df["updated_at"] = pd.Timestamp.now("UTC")

        result = ensure_warehouse_table(engine, "dim_teams", df, pk_cols=("team_id",))

        # One ALTER TABLE per missing column, both inside a single transaction.
        engine.begin.assert_called_once()
        conn = engine.begin.return_value.__enter__.return_value
        assert conn.execute.call_count == 2
        executed_sql = " ".join(str(call.args[0]) for call in conn.execute.call_args_list)
        assert "ADD COLUMN" in executed_sql
        assert "ingested_at" in executed_sql and "updated_at" in executed_sql
        assert "CURRENT_TIMESTAMP" in executed_sql

        # Returned table is the freshly re-reflected one, not the stale one.
        assert result is migrated_table
        assert mock_table.call_count == 2

    @patch("pipelines.load.warehouse.Table")
    @patch("pipelines.load.warehouse.sa_inspect")
    def test_no_alter_table_when_audit_columns_already_present(self, mock_sa_inspect, mock_table):
        from pipelines.load.warehouse import ensure_warehouse_table, AUDIT_COLUMNS

        mock_inspector = MagicMock()
        mock_inspector.has_table.return_value = True
        mock_sa_inspect.return_value = mock_inspector

        up_to_date_table = MagicMock(name="up_to_date_table")
        cols = [MagicMock() for _ in range(3)]
        for col_mock, name in zip(cols, ("team_id", *AUDIT_COLUMNS)):
            col_mock.name = name
        up_to_date_table.columns = cols
        mock_table.return_value = up_to_date_table

        engine = MagicMock()
        df = pd.DataFrame([{"team_id": 1, "team_name": "X", "short_name": "X", "team_logo_url": "u"}])

        result = ensure_warehouse_table(engine, "dim_teams", df, pk_cols=("team_id",))

        engine.begin.assert_not_called()
        assert mock_table.call_count == 1  # reflected once, no re-reflect needed
        assert result is up_to_date_table


# ---------------------------------------------------------------------
# 3. CRUD & Lineage Operations
# ---------------------------------------------------------------------

class TestApplyAuditColumns:
    """Pure pandas logic, no DB — `apply_audit_columns` is the mechanism
    that guarantees a raw seed DataFrame (no ingested_at/updated_at at
    all) is safe to hand to the warehouse loader."""

    def test_injects_missing_columns_with_one_consistent_batch_timestamp(self, raw_team_frame_missing_audit_columns):
        df = raw_team_frame_missing_audit_columns
        assert "ingested_at" not in df.columns and "updated_at" not in df.columns

        out = apply_audit_columns(df)

        for col in AUDIT_COLUMNS:
            assert col in out.columns
            assert out[col].notna().all()
        # Original frame must not be mutated.
        assert "ingested_at" not in df.columns

    def test_preserves_existing_ingested_at_and_updated_at(self):
        original = pd.Timestamp("2026-01-01T00:00:00Z")
        df = pd.DataFrame([{"x": 1, "ingested_at": original, "updated_at": original}])
        out = apply_audit_columns(df, as_of=pd.Timestamp("2026-06-01T00:00:00Z"))
        assert out["ingested_at"].iloc[0] == original
        assert out["updated_at"].iloc[0] == original


class TestUpsertBehaviorAgainstRealSQLite:
    """The literal scenario from the task, exercised against the real
    `upsert_table` production code path (dialect-aware, see
    `pipelines/load/warehouse.py`), not a mock or a parallel
    reimplementation — SQLite genuinely creates the table, runs the
    staging round-trip, and executes a real `ON CONFLICT DO UPDATE`.
    """

    def test_raw_dataframe_without_audit_columns_gets_populated_on_write(self, sqlite_engine, raw_team_frame_missing_audit_columns):
        from pipelines.load.warehouse import ensure_schemas, upsert_table, warehouse_table_name
        from sqlalchemy import text

        ensure_schemas(sqlite_engine)
        raw = raw_team_frame_missing_audit_columns
        assert "ingested_at" not in raw.columns and "updated_at" not in raw.columns

        upsert_table(sqlite_engine, "dim_teams", raw)

        table = warehouse_table_name(sqlite_engine, "dim_teams")
        with sqlite_engine.connect() as conn:
            row = conn.execute(text(f'SELECT * FROM "{table}" WHERE team_id = 1')).mappings().first()

        assert row is not None, "row was not written"
        assert row["ingested_at"] is not None
        assert row["updated_at"] is not None

    def test_upsert_twice_with_identical_data_keeps_row_count_unchanged(self, sqlite_engine, raw_team_frame_missing_audit_columns):
        from pipelines.load.warehouse import ensure_schemas, upsert_table, warehouse_table_name
        from sqlalchemy import text

        ensure_schemas(sqlite_engine)
        table = warehouse_table_name(sqlite_engine, "dim_teams")

        upsert_table(sqlite_engine, "dim_teams", raw_team_frame_missing_audit_columns.copy())
        upsert_table(sqlite_engine, "dim_teams", raw_team_frame_missing_audit_columns.copy())  # identical batch again

        with sqlite_engine.connect() as conn:
            count = conn.execute(text(f'SELECT COUNT(*) FROM "{table}" WHERE team_id = 1')).scalar()
        assert count == 1, "re-running an identical upsert must not duplicate rows"

    def test_upsert_twice_with_unchanged_data_does_not_bump_updated_at(self, sqlite_engine, raw_team_frame_missing_audit_columns):
        """Direct answer to "will re-running the pipeline with no new
        results still touch updated_at?": it must not. Re-upserting
        byte-for-byte identical business-column values a second time
        must leave the row's `updated_at` (and `ingested_at`) exactly as
        they were — see `_build_change_detection_clause`."""
        from pipelines.load.warehouse import ensure_schemas, upsert_table, warehouse_table_name
        from sqlalchemy import text

        ensure_schemas(sqlite_engine)
        table = warehouse_table_name(sqlite_engine, "dim_teams")

        upsert_table(sqlite_engine, "dim_teams", raw_team_frame_missing_audit_columns.copy())
        with sqlite_engine.connect() as conn:
            first = dict(conn.execute(text(f'SELECT * FROM "{table}" WHERE team_id = 1')).mappings().first())

        # Sleep past a real clock tick — if updated_at *did* wrongly
        # bump, this makes that failure deterministic rather than
        # possibly hidden by two calls landing in the same second.
        time.sleep(1.1)

        upsert_table(sqlite_engine, "dim_teams", raw_team_frame_missing_audit_columns.copy())
        with sqlite_engine.connect() as conn:
            second = dict(conn.execute(text(f'SELECT * FROM "{table}" WHERE team_id = 1')).mappings().first())

        assert str(second["updated_at"]) == str(first["updated_at"]), (
            "updated_at must NOT change when re-upserted data is identical to what's already stored"
        )
        assert str(second["ingested_at"]) == str(first["ingested_at"])

    def test_upsert_with_changed_column_updates_ingested_at_preserved_updated_at_advances(
        self, sqlite_engine, raw_team_frame_missing_audit_columns
    ):
        from pipelines.load.warehouse import ensure_schemas, upsert_table, warehouse_table_name
        from sqlalchemy import text

        ensure_schemas(sqlite_engine)
        table = warehouse_table_name(sqlite_engine, "dim_teams")

        upsert_table(sqlite_engine, "dim_teams", raw_team_frame_missing_audit_columns.copy())
        with sqlite_engine.connect() as conn:
            first = dict(conn.execute(text(f'SELECT * FROM "{table}" WHERE team_id = 1')).mappings().first())

        # SQLite's CURRENT_TIMESTAMP has one-second granularity — sleep
        # past a second boundary so "updated_at advances" is a
        # deterministic strict-greater-than, not a flaky same-second tie.
        time.sleep(1.1)

        second_df = raw_team_frame_missing_audit_columns.copy()
        second_df["team_name"] = "Harmonized United Renamed"
        upsert_table(sqlite_engine, "dim_teams", second_df)

        with sqlite_engine.connect() as conn:
            count = conn.execute(text(f'SELECT COUNT(*) FROM "{table}" WHERE team_id = 1')).scalar()
            second = dict(conn.execute(text(f'SELECT * FROM "{table}" WHERE team_id = 1')).mappings().first())

        assert count == 1, "an upsert of an existing PK must update in place, not insert a second row"
        assert second["team_name"] == "Harmonized United Renamed"
        assert str(second["ingested_at"]) == str(first["ingested_at"]), "ingested_at must never change on conflict"
        assert str(second["updated_at"]) > str(first["updated_at"]), "updated_at must advance on conflict"

    def test_ensure_schemas_is_idempotent(self, sqlite_engine):
        from pipelines.load.warehouse import ensure_schemas
        ensure_schemas(sqlite_engine)
        ensure_schemas(sqlite_engine)  # must not raise on a second call


class TestCrossSeasonSyntheticIdCollisionRegression:
    """Regression test for a real production bug: `fact_match_events`,
    `fact_shot_events`, and `bridge_player_seasons` each generate their
    id column as a synthetic sequential counter starting at 0 (or 1) on
    every single build call — see pipelines/transform/fixtures.py and
    pipelines/transform/players.py — so two different seasons' builds
    both produce ids like "event_0", "event_1", .... Under a
    single-column primary key, upserting one season silently overwrote
    the other wherever their id ranges overlapped (confirmed live: a
    665-vs-1207-row loss in `bridge_player_seasons`, and 11 missing
    fixtures' worth of events in `fact_match_events`/`fact_shot_events`
    — in both cases the source CSVs had complete data; only the
    upserted warehouse state was corrupted).

    The fix is `schema.py` scoping these three tables' primary keys by
    `season_id` (the id is already guaranteed unique *within* one build
    call, so this is sufficient) — this test reproduces the exact
    collision shape and confirms both seasons' rows now survive.
    """

    def test_colliding_local_ids_across_two_seasons_both_survive(self, sqlite_engine):
        from pipelines.load.warehouse import ensure_schemas, upsert_table, warehouse_table_name
        from sqlalchemy import text

        ensure_schemas(sqlite_engine)

        # fact_match_events now has real FK constraints (dim_fixtures,
        # dim_teams, dim_players) — set up the referenced rows first,
        # same dependency order the real pipeline always upserts in.
        upsert_table(sqlite_engine, "dim_seasons", pd.DataFrame([
            {"season_id": 777, "season_name": "2025/26", "competition_name": "Premier League"},
            {"season_id": 841, "season_name": "2026/27", "competition_name": "Premier League"},
        ]))
        upsert_table(sqlite_engine, "dim_teams", pd.DataFrame([
            {"team_id": 1, "team_name": "Team One", "short_name": "ONE", "team_logo_url": "x"},
            {"team_id": 2, "team_name": "Team Two", "short_name": "TWO", "team_logo_url": "x"},
        ]))
        upsert_table(sqlite_engine, "dim_players", pd.DataFrame([
            {"player_id": 10, "player_name": "Player Ten", "date_of_birth": None, "country": None, "player_photo_url": None},
            {"player_id": 20, "player_name": "Player Twenty", "date_of_birth": None, "country": None, "player_photo_url": None},
        ]))
        upsert_table(sqlite_engine, "dim_fixtures", pd.DataFrame([
            {"fixture_id": fid, "season_id": season, "gameweek": 1, "kickoff_datetime": "2026-01-01 12:00:00",
             "stadium": "X", "attendance": 1000, "home_team_id": 1, "away_team_id": 2, "fixture_status": "C"}
            for season, fids in ((777, range(100, 103)), (841, range(900, 902)))
            for fid in fids
        ]))

        def _match_event_row(fixture_id, season_id, team_id, scorer_player_id, minute, local_index):
            return {
                "match_event_id": f"fixture_{fixture_id}_event_{local_index}",
                "fixture_id": fixture_id, "season_id": season_id, "team_id": team_id, "event_type": "goal",
                "scorer_player_id": scorer_player_id, "assist_player_id": None, "own_goal_player_id": None,
                "carded_player_id": None, "player_on_id": None, "player_off_id": None,
                "minute": minute, "minute_display": str(minute), "is_stoppage_time": False,
            }

        # Season 777 "builds" 3 events, one per fixture — ids are
        # fixture-scoped (fixture_100_event_0, fixture_101_event_0, ...).
        season_777_events = pd.DataFrame([
            _match_event_row(100 + i, 777, 1, 10, 10 + i, 0) for i in range(3)
        ])
        # Season 841 independently "builds" its own 2 events. Before the
        # fix, id collisions came from a season-wide counter both seasons
        # shared (event_0, event_1, ...); scoping by fixture_id already
        # prevents that regardless of season — this test still exercises
        # the season_id-scoped primary key as defense-in-depth.
        season_841_events = pd.DataFrame([
            _match_event_row(900 + i, 841, 2, 20, 20 + i, 0) for i in range(2)
        ])

        upsert_table(sqlite_engine, "fact_match_events", season_777_events)
        upsert_table(sqlite_engine, "fact_match_events", season_841_events)

        table = warehouse_table_name(sqlite_engine, "fact_match_events")
        with sqlite_engine.connect() as conn:
            total = conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar()
            season_777_count = conn.execute(text(f'SELECT COUNT(*) FROM "{table}" WHERE season_id = 777')).scalar()
            season_841_count = conn.execute(text(f'SELECT COUNT(*) FROM "{table}" WHERE season_id = 841')).scalar()

        assert season_777_count == 3, "season 777's rows must survive a later season's upsert"
        assert season_841_count == 2
        assert total == 5, f"expected both seasons' rows to coexist (3 + 2 = 5), got {total} — the collision bug is back"
