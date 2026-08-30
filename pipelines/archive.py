import argparse
import shutil
from pathlib import Path

from pipelines.pipeline import run_pipeline
from pipelines.snapshot import export_csv_snapshot
from pipelines.schema import materialize_frames, snapshot_season_slug

DEFAULT_HISTORICAL_ROOT = Path("data/historical")
DEFAULT_SNAPSHOT_ROOT = Path("data/snapshots")

def archive_season(
    season_id: int,
    historical_root: Path = DEFAULT_HISTORICAL_ROOT,
    snapshot_root: Path = DEFAULT_SNAPSHOT_ROOT,
) -> Path:
    print(f"[ARCHIVE] Running final full-season transform for season_id={season_id}...")
    frames = run_pipeline(season_id)
    materialized = materialize_frames(frames)
    slug = snapshot_season_slug(materialized, season_id)

    print(f"[ARCHIVE] Writing final versioned snapshot (season slug: {slug})...")
    snapshot_dir = export_csv_snapshot(frames=frames, season_id=season_id, base_dir=str(snapshot_root))

    historical_dir = historical_root / slug
    if historical_dir.exists():
        raise FileExistsError(
            f"{historical_dir} already exists — refusing to overwrite an existing historical "
            f"archive. Remove it first if this season genuinely needs re-archiving."
        )

    print(f"[ARCHIVE] Copying {snapshot_dir} -> {historical_dir}")
    shutil.copytree(snapshot_dir, historical_dir)

    print(f"\n[SUCCESS] season_id={season_id} archived as data/historical/{slug}/")
    print(f"          Run `python -m pipelines.seed --season {slug}` to (re)load it into the warehouse.")
    return historical_dir

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Archive a completed live season into data/historical/.")
    parser.add_argument("--season-id", type=int, required=True,
                         help="Pulse competition season id for the season that just finished.")
    args = parser.parse_args(argv)
    archive_season(args.season_id)

if __name__ == "__main__":
    main()