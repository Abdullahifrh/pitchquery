import argparse

from pipelines.pipeline import run_pipeline
from pipelines.load.warehouse import upsert_frames
from pipelines.snapshot import export_csv_snapshot

DEFAULT_SNAPSHOT_BASE_DIR = "data/snapshots"

def run_live(
    season_id: int,
    export_snapshots: bool = True,
    snapshot_base_dir: str = DEFAULT_SNAPSHOT_BASE_DIR,
) -> dict:
    print(f"[LIVE] Running full pipeline for season_id={season_id}...")
    frames = run_pipeline(season_id)

    print("[LIVE] Upserting into warehouse (schema-validated, idempotent)...")
    upsert_frames(frames)
    print(f"[SUCCESS] Live warehouse refresh complete for season_id={season_id}.")

    if export_snapshots:
        print("[LIVE] Writing Bronze/landing CSV snapshot (best-effort, non-fatal)...")
        try:
            snapshot_dir = export_csv_snapshot(
                frames=frames, season_id=season_id, base_dir=snapshot_base_dir
            )
            print(f"[LIVE] Snapshot written to {snapshot_dir}")
        except Exception as exc:
            print(f"  [WARN] run_live: snapshot export failed (warehouse refresh is unaffected): {exc!r}")

    return frames

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Refresh the warehouse for the current live season.")
    parser.add_argument(
        "--season-id",
        type=int,
        default=777,
        help="Pulse competition season id for the active live season.",
    )
    parser.add_argument(
        "--no-export-snapshots",
        action="store_false",
        dest="export_snapshots",
        help="Skip writing the Bronze/landing CSV snapshot after this run (written by default).",
    )
    parser.add_argument(
        "--snapshot-base-dir",
        type=str,
        default=DEFAULT_SNAPSHOT_BASE_DIR,
        help="Base directory for CSV snapshot runs.",
    )
    parser.set_defaults(export_snapshots=True)
    args = parser.parse_args(argv)
    run_live(args.season_id, export_snapshots=args.export_snapshots, snapshot_base_dir=args.snapshot_base_dir)

if __name__ == "__main__":
    main()