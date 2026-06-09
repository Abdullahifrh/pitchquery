import json
import pytest
from sqlalchemy import create_engine, text
from pipelines.load_to_db import get_db_engine, discover_latest_snapshot, WAREHOUSE_SCHEMA

@pytest.fixture(scope="session")
def engine():
    return get_db_engine()

@pytest.fixture(scope="session")
def manifest():
    snapshot_dir = discover_latest_snapshot()
    return json.loads((snapshot_dir / "manifest.json").read_text(encoding="utf-8"))

def fetch_count(engine, table_name: str) -> int:
    with engine.connect() as conn:
        result = conn.execute(text(f'SELECT COUNT(*) FROM {WAREHOUSE_SCHEMA}."{table_name}"'))
        return int(result.scalar())

def test_row_counts_match_snapshot(engine, manifest):
    """Ensures all rows from CSV successfully reached the warehouse"""
    for table_name, meta in manifest["tables"].items():
        db_count = fetch_count(engine, table_name)
        assert db_count == int(meta["rows"]), f"Count mismatch in {table_name}"

def test_fact_team_match_stats_integrity(engine):
    """Ensures foreign keys in fact_team_match_stats resolve to dimensions"""
    query = text(f'''
        SELECT COUNT(*)
        FROM {WAREHOUSE_SCHEMA}.fact_team_match_stats f
        LEFT JOIN {WAREHOUSE_SCHEMA}.dim_teams t ON t.team_id = f.team_id
        LEFT JOIN {WAREHOUSE_SCHEMA}.dim_fixtures fx ON fx.fixture_id = f.fixture_id
        WHERE t.team_id IS NULL OR fx.fixture_id IS NULL
    ''')
    
    with engine.connect() as conn:
        orphans = conn.execute(query).scalar()
        
    assert orphans == 0, f"Found {orphans} orphaned records in fact_team_match_stats (broken joins)"