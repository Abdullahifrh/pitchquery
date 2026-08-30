import json
import os
import weakref
from pathlib import Path
from dotenv import load_dotenv

import pandas as pd
from sqlalchemy import (
    create_engine, MetaData, Table, Column, ForeignKey, text, Date, DateTime,
    cast, select, or_, and_, inspect as sa_inspect
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from pipelines.schema import (
    primary_key_columns, materialize_frames, SchemaValidationError,
    AUDIT_COLUMNS, apply_audit_columns, FOREIGN_KEYS,
)
from pipelines.utils import map_pandas_to_sqlalchemy, coerce_id_columns

load_dotenv()

WAREHOUSE_SCHEMA = "warehouse"
STAGING_SCHEMA = "staging"
DEFAULT_SNAPSHOT_ROOT = Path("data/snapshots")

# One shared MetaData per engine (not fresh per call)
_metadata_by_engine: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()

def _metadata_for_engine(engine) -> MetaData:
    if engine not in _metadata_by_engine:
        _metadata_by_engine[engine] = MetaData(schema=_schema_for(engine, WAREHOUSE_SCHEMA))
    return _metadata_by_engine[engine]

def _is_sqlite(engine) -> bool:
    return engine.dialect.name == "sqlite"

def _schema_for(engine, schema_name: str) -> str | None:
    """SQLite has no multi-schema concept — collapse to the default namespace; Postgres keeps real schemas."""
    return None if _is_sqlite(engine) else schema_name

def _physical_table_name(engine, table_name: str, schema_name: str) -> str:
    """SQLite: prefixed with the logical schema name (warehouse__foo); Postgres: unchanged."""
    return f"{schema_name}__{table_name}" if _is_sqlite(engine) else table_name

def _dialect_insert(engine, table):
    """Only which dialect's insert() constructor gets called differs — the rest of the call shape is identical."""
    return sqlite_insert(table) if _is_sqlite(engine) else pg_insert(table)

def warehouse_table_name(engine, table_name: str) -> str:
    """Public helper so callers never need to hardcode the SQLite warehouse__ naming convention themselves."""
    return _physical_table_name(engine, table_name, WAREHOUSE_SCHEMA)

def _foreign_key_target(engine, ref_table: str, ref_col: str) -> str:
    """Builds the string SQLAlchemy's ForeignKey() expects, using the same dialect-aware physical naming."""
    physical = _physical_table_name(engine, ref_table, WAREHOUSE_SCHEMA)
    schema = _schema_for(engine, WAREHOUSE_SCHEMA)
    return f"{schema}.{physical}.{ref_col}" if schema else f"{physical}.{ref_col}"

def get_db_engine():
    """Creates the database engine using credentials from environment."""
    user = os.getenv("DB_USER", "football")
    password = os.getenv("DB_PASSWORD")
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    db_name = os.getenv("DB_NAME", "premierleague")

    if not password:
        raise ValueError("DB_PASSWORD not found in environment.")
    
    uri = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db_name}"
    return create_engine(uri)

def discover_latest_snapshot(snapshot_root: Path = DEFAULT_SNAPSHOT_ROOT) -> Path:
    """Returns the path to the newest snapshot folder."""
    if not snapshot_root.exists():
        raise FileNotFoundError(f"Snapshot root not found: {snapshot_root}")

    manifests = list(snapshot_root.glob("season=*/run=*/manifest.json"))
    if not manifests:
        raise FileNotFoundError("No snapshots found. Run your pipeline --export-snapshots first.")

    latest_manifest = max(manifests, key=lambda p: p.parent.name)
    return latest_manifest.parent

def _retrofit_missing_audit_columns(engine, table_name: str, physical_name: str, schema, table):
    """Adds ingested_at/updated_at via ALTER TABLE to a table created before AUDIT_COLUMNS existed.

    Deliberately narrow — only these two columns, this one way. Not a
    general migration engine; a business-column or PK change needs an
    explicit, reviewed step instead.
    """
    existing_cols = {c.name for c in table.columns}
    missing = [c for c in AUDIT_COLUMNS if c not in existing_cols]
    if not missing:
        return table

    qualified_name = f'{schema}."{physical_name}"' if schema else f'"{physical_name}"'
    column_type_sql = "DATETIME" if _is_sqlite(engine) else "TIMESTAMPTZ"
    with engine.begin() as conn:
        for col_name in missing:
            print(f"  [MIGRATE] {table_name}: adding missing column '{col_name}' ({column_type_sql} DEFAULT CURRENT_TIMESTAMP)")
            conn.execute(text(f'ALTER TABLE {qualified_name} ADD COLUMN "{col_name}" {column_type_sql} DEFAULT CURRENT_TIMESTAMP'))

    # Re-reflect rather than patch the in-memory Table by hand, using the
    # shared per-engine MetaData so the table stays FK-discoverable later in this run.
    return Table(
        physical_name, _metadata_for_engine(engine),
        autoload_with=engine, schema=schema, extend_existing=True,
    )

def ensure_warehouse_table(engine, table_name: str, df: pd.DataFrame, pk_cols: tuple):
    """Returns the warehouse Table object for `table_name`.

    If the table already exists, its structure is REFLECTED from the
    database (not re-inferred from this batch's dtypes) — an empty
    batch would otherwise infer an all-VARCHAR table and break FK/type
    casts downstream. Only created from this batch's inferred types
    when the table is new; raises if that first-ever batch is empty,
    since types can't be inferred from nothing.
    """
    metadata = _metadata_for_engine(engine)
    physical_name = _physical_table_name(engine, table_name, WAREHOUSE_SCHEMA)
    schema = _schema_for(engine, WAREHOUSE_SCHEMA)

    if sa_inspect(engine).has_table(physical_name, schema=schema):
        table = Table(physical_name, metadata, autoload_with=engine, schema=schema, extend_existing=True)
        return _retrofit_missing_audit_columns(engine, table_name, physical_name, schema, table)

    if df.empty:
        raise SchemaValidationError(
            f"{table_name}: cannot create this warehouse table for the first time from an "
            f"empty DataFrame (0 rows) — column types can't be inferred without any data. "
            f"Run `pipelines.seed` (or any pipeline run that produces real rows for this "
            f"table) at least once before a run that may legitimately produce zero rows "
            f"(e.g. `run_live` very early in a season, before this fact type has any data)."
        )

    columns = []
    fk_defs = {local_col: (ref_table, ref_col) for local_col, ref_table, ref_col in FOREIGN_KEYS.get(table_name, ())}
    for col in df.columns:
        column_kwargs = {"primary_key": col in pk_cols}
        if col in AUDIT_COLUMNS:
            column_kwargs["server_default"] = text("CURRENT_TIMESTAMP")
        col_args = [col, map_pandas_to_sqlalchemy(col, df[col])]
        if col in fk_defs:
            ref_table, ref_col = fk_defs[col]
            col_args.append(ForeignKey(_foreign_key_target(engine, ref_table, ref_col)))
        columns.append(Column(*col_args, **column_kwargs))

    # Second line of defense: a table's DDL must have these columns
    # regardless of this batch (upsert_table already guarantees they're on df).
    for audit_col in AUDIT_COLUMNS:
        if audit_col not in df.columns:
            columns.append(Column(audit_col, DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")))

    table = Table(physical_name, metadata, *columns, schema=schema, extend_existing=True)
    table.create(engine, checkfirst=True)
    return table

def _coerce_column_for_sqlite(series: pd.Series, sa_type) -> pd.Series:
    """SQLite-side equivalent of Postgres's CAST — SQLite's DBAPI binding requires a real
    Python date/datetime object for Date/DateTime columns rather than parsing a string.
    """
    if isinstance(sa_type, DateTime):
        return pd.to_datetime(series, errors="coerce")
    if isinstance(sa_type, Date):
        return pd.to_datetime(series, errors="coerce").dt.date
    return series

def _records_for_sqlite(df: pd.DataFrame) -> list[dict]:
    """Converts a DataFrame to dicts with plain Python scalars, since sqlite3's DBAPI rejects numpy/pandas types."""
    import numpy as np

    def _to_native(value):
        if pd.isna(value):
            return None
        if isinstance(value, pd.Timestamp):
            return value.to_pydatetime()
        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.floating):
            return float(value)
        if isinstance(value, np.bool_):
            return bool(value)
        return value

    return [{k: _to_native(v) for k, v in row.items()} for row in df.to_dict(orient="records")]

def _build_update_dict(stmt, pk_cols: tuple) -> dict:
    """Shared ON CONFLICT DO UPDATE SET-clause construction for both Postgres and SQLite upsert paths."""
    preserve_on_conflict = pk_cols + ("ingested_at",)
    return {
        c.name: (text("CURRENT_TIMESTAMP") if c.name == "updated_at" else c)
        for c in stmt.excluded
        if c.name not in preserve_on_conflict
    }

def _column_is_distinct(existing_col, incoming_col):
    """NULL-safe "these two values differ" check, built from =/IS NULL/AND/OR rather than
    SQLAlchemy's is_distinct_from() (relies on SQLite 3.39+ syntax this project can't assume).
    """
    return or_(
        and_(existing_col.is_(None), incoming_col.isnot(None)),
        and_(existing_col.isnot(None), incoming_col.is_(None)),
        and_(existing_col.isnot(None), incoming_col.isnot(None), existing_col != incoming_col),
    )

def _build_change_detection_clause(table, stmt, update_dict: dict):
    """Gates ON CONFLICT DO UPDATE on a real business column actually differing (an idle
    re-run then leaves already-correct rows untouched). Returns None (no WHERE, always
    updates) if a table has no business columns beyond its PK/audit columns.
    """
    business_cols = [name for name in update_dict if name != "updated_at"]
    if not business_cols:
        return None
    return or_(*(_column_is_distinct(table.c[name], stmt.excluded[name]) for name in business_cols))

def upsert_table(engine, table_name: str, df: pd.DataFrame):
    """Performs the incremental ELT loading process. Calls apply_audit_columns unconditionally
    (usually a no-op re-confirmation) so a raw DataFrame never breaks the staging/warehouse
    column match. Postgres path stages + CASTs before INSERT ... ON CONFLICT DO UPDATE;
    SQLite builds the upsert directly from rows, with Date/DateTime coerced separately.
    """
    pk_cols = primary_key_columns(table_name)
    df = apply_audit_columns(df)
    warehouse_table = ensure_warehouse_table(engine, table_name, df, pk_cols)

    if df.empty:
        # Still worth calling ensure_warehouse_table above (first-ever empty batch), but no
        # reason to round-trip an empty frame through staging just to insert zero rows.
        print(f"  ({table_name}: 0 rows, nothing to upsert)")
        return

    if _is_sqlite(engine):
        coerced_df = df.copy()
        for col in warehouse_table.columns:
            if col.name in coerced_df.columns:
                coerced_df[col.name] = _coerce_column_for_sqlite(coerced_df[col.name], col.type)
        stmt = _dialect_insert(engine, warehouse_table).values(_records_for_sqlite(coerced_df))
    else:
        # Load to staging, reflect it, then CAST each column to the real warehouse type.
        staging_physical_name = _physical_table_name(engine, table_name, STAGING_SCHEMA)
        staging_schema = _schema_for(engine, STAGING_SCHEMA)
        df.to_sql(name=staging_physical_name, con=engine, schema=staging_schema, if_exists="replace", index=False)

        metadata = MetaData(schema=staging_schema)
        staging_table = Table(staging_physical_name, metadata, autoload_with=engine, schema=staging_schema)

        casted_cols = [cast(staging_table.c[col.name], col.type) for col in warehouse_table.columns]

        stmt = _dialect_insert(engine, warehouse_table).from_select(
            warehouse_table.columns.keys(),
            select(*casted_cols)
        )

    # ingested_at is preserved on conflict (set once, on first insert); updated_at is
    # the literal SQL CURRENT_TIMESTAMP, not the batch's own clock — see schema.AUDIT_COLUMNS.
    update_dict = _build_update_dict(stmt, pk_cols)

    # Only fires when a real business column's value actually changed — see _build_change_detection_clause.
    change_clause = _build_change_detection_clause(warehouse_table, stmt, update_dict) if update_dict else None

    if update_dict:
        upsert_stmt = stmt.on_conflict_do_update(
            index_elements=pk_cols,
            set_=update_dict,
            where=change_clause,
        )
    else:
        upsert_stmt = stmt.on_conflict_do_nothing(index_elements=pk_cols)
        
    with engine.begin() as conn:
        conn.execute(upsert_stmt)

def ensure_schemas(engine):
    if _is_sqlite(engine):
        # No CREATE SCHEMA concept in SQLite — already collapsed to a flat, prefixed namespace.
        return
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {WAREHOUSE_SCHEMA}"))
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {STAGING_SCHEMA}"))

def load_latest_snapshot():
    """Historical/full-season path: load every table out of the most recent CSV snapshot folder."""
    engine = get_db_engine()
    ensure_schemas(engine)

    snapshot_dir = discover_latest_snapshot()
    manifest = json.loads((snapshot_dir / "manifest.json").read_text(encoding="utf-8"))
    
    print(f"[POSTGRES LOADER] Discovered: {snapshot_dir}")
    
    for table_name in manifest["tables"].keys():
        print(f"Processing {table_name}...")
        df = coerce_id_columns(pd.read_csv(snapshot_dir / f"{table_name}.csv"))
        upsert_table(engine, table_name, df)
        
    print("\n[SUCCESS] PostgreSQL load complete!")

def upsert_frames(frames: dict[str, pd.DataFrame], engine=None) -> None:
    """Live-ingestion path: upsert an in-memory {table_name: DataFrame} dict directly, no CSV round-trip."""
    engine = engine or get_db_engine()
    ensure_schemas(engine)

    materialized = materialize_frames(frames)
    for table_name, df in materialized.items():
        print(f"Processing {table_name}...")
        upsert_table(engine, table_name, df)

    print("\n[SUCCESS] Live PostgreSQL upsert complete!")

if __name__ == "__main__":
    load_latest_snapshot()
