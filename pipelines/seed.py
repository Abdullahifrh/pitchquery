import argparse
from pathlib import Path

import pandas as pd

from pipelines.schema import contract_table_names, validate_materialized_frames, apply_audit_columns
from pipelines.load.warehouse import get_db_engine, ensure_schemas, upsert_table
from pipelines.utils import coerce_id_columns

DEFAULT_HISTORICAL_ROOT = Path("data/historical")

def load_historical_frames(season_dir: Path) -> dict[str, pd.DataFrame]:
    if not season_dir.exists():
        raise FileNotFoundError(
            f"Historical season directory not found: {season_dir}. "
            f"Unpack the season CSV archive into this path first."
        )

    frames: dict[str, pd.DataFrame] = {}
    for table_name in contract_table_names():
        csv_path = season_dir / f"{table_name}.csv"
        if not csv_path.exists():
            raise FileNotFoundError(f"Missing expected historical CSV: {csv_path}")
        # Undoes the CSV-round-trip float-upcast quirk on missing *_id values 
        frames[table_name] = coerce_id_columns(pd.read_csv(csv_path))
    return frames

def seed_season(season: str, historical_root: Path = DEFAULT_HISTORICAL_ROOT) -> None:
    season_dir = historical_root / season
    print(f"[SEED] Loading historical frames from {season_dir}")
    frames = load_historical_frames(season_dir)

    # These CSVs already went through materialize_frames once (export_csv_snapshot
    # wrote them) — validate instead of re-materializing, which would fail on rename.
    materialized = validate_materialized_frames(frames)

    # Historical CSVs predate the audit columns — stamp them fresh at seed time,
    # one shared timestamp per run.
    seed_as_of = pd.Timestamp.utcnow()
    materialized = {
        table_name: apply_audit_columns(df, as_of=seed_as_of)
        for table_name, df in materialized.items()
    }

    engine = get_db_engine()
    ensure_schemas(engine)

    for table_name, df in materialized.items():
        print(f"[SEED] Upserting {table_name} ({len(df)} rows)...")
        upsert_table(engine, table_name, df)

    print(f"\n[SUCCESS] Seeded season={season} into the warehouse.")

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Seed the warehouse from a historical season archive.")
    parser.add_argument(
        "--season",
        type=str,
        default="2025_26",
        help="Season slug matching a data/historical/<season>/ directory.",
    )
    parser.add_argument(
        "--historical-dir",
        type=str,
        default=str(DEFAULT_HISTORICAL_ROOT),
        help="Root directory containing per-season historical CSV folders.",
    )
    args = parser.parse_args(argv)
    seed_season(args.season, Path(args.historical_dir))

if __name__ == "__main__":
    main()
