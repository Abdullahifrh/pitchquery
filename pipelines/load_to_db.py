import json
import os
from pathlib import Path
from dotenv import load_dotenv

import pandas as pd
from sqlalchemy import (
    create_engine, MetaData, Table, Column, text,
    cast, select
)
from sqlalchemy.dialects.postgresql import insert
from pipelines.schema import primary_key_columns
from pipelines.utils import map_pandas_to_sqlalchemy

load_dotenv()

WAREHOUSE_SCHEMA = "warehouse"
STAGING_SCHEMA = "staging"
DEFAULT_SNAPSHOT_ROOT = Path("data/snapshots")

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
    """Returns the path to the newest snapshot folder"""
    if not snapshot_root.exists():
        raise FileNotFoundError(f"Snapshot root not found: {snapshot_root}")

    manifests = list(snapshot_root.glob("season=*/run=*/manifest.json"))
    if not manifests:
        raise FileNotFoundError("No snapshots found. Run your pipeline --export-snapshots first.")

    latest_manifest = max(manifests, key=lambda p: p.parent.name)
    return latest_manifest.parent

def ensure_warehouse_table(engine, table_name: str, df: pd.DataFrame, pk_cols: tuple):
    """Creates the warehouse table structure if it doesn't exist"""
    metadata = MetaData(schema=WAREHOUSE_SCHEMA)
    columns = [
        Column(col, map_pandas_to_sqlalchemy(col, df[col]), primary_key=(col in pk_cols))
        for col in df.columns
    ]
    table = Table(table_name, metadata, *columns)
    table.create(engine, checkfirst=True)
    return table

def upsert_table(engine, table_name: str, df: pd.DataFrame):
    """Performs the incremental ELT loading process"""
    pk_cols = primary_key_columns(table_name)
    warehouse_table = ensure_warehouse_table(engine, table_name, df, pk_cols)
    
    # Load to Staging
    df.to_sql(name=table_name, con=engine, schema=STAGING_SCHEMA, if_exists="replace", index=False)
    
    # Reflect Staging Table
    metadata = MetaData(schema=STAGING_SCHEMA)
    staging_table = Table(table_name, metadata, autoload_with=engine)
    
    casted_cols = [cast(staging_table.c[col.name], col.type) for col in warehouse_table.columns]
    
    # Upsert Statement
    stmt = insert(warehouse_table).from_select(
        warehouse_table.columns.keys(),
        select(*casted_cols)
    )
    
    update_dict = {c.name: c for c in stmt.excluded if c.name not in pk_cols}
    
    upsert_stmt = stmt.on_conflict_do_update(
        index_elements=pk_cols, 
        set_=update_dict
    ) if update_dict else stmt.on_conflict_do_nothing(index_elements=pk_cols)
        
    with engine.begin() as conn:
        conn.execute(upsert_stmt)

def load_latest_snapshot():

    engine = get_db_engine()
    
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {WAREHOUSE_SCHEMA}"))
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {STAGING_SCHEMA}"))
        
    snapshot_dir = discover_latest_snapshot()
    manifest = json.loads((snapshot_dir / "manifest.json").read_text(encoding="utf-8"))
    
    print(f"[POSTGRES LOADER] Discovered: {snapshot_dir}")
    
    for table_name in manifest["tables"].keys():
        print(f"Processing {table_name}...")
        df = pd.read_csv(snapshot_dir / f"{table_name}.csv")
        upsert_table(engine, table_name, df)
        
    print("\n[SUCCESS] PostgreSQL load complete!")

if __name__ == "__main__":
    load_latest_snapshot()