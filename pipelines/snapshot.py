import json
from pathlib import Path
from typing import Any
import pandas as pd
from pipelines.schema import SCHEMA_VERSION, materialize_frames, snapshot_season_slug


def timestamp_run_id() -> str:
    ts = pd.Timestamp.utcnow()
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.strftime("%Y%m%d_%H%M%S")


def export_csv_snapshot(
    frames: dict[str, pd.DataFrame],
    season_id: int,
    base_dir: str | Path = "data/snapshots",
) -> Path:
    """Materialize warehouse tables and persist a versioned CSV snapshot plus manifest"""

    materialized = materialize_frames(frames)
    season_slug = snapshot_season_slug(materialized, season_id)
    run_id = timestamp_run_id()

    snapshot_dir = Path(base_dir) / f"season={season_slug}" / f"run={run_id}"
    snapshot_dir.mkdir(parents=True, exist_ok=False)

    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "season_id": int(season_id),
        "season_slug": season_slug,
        "run_id": run_id,
        "created_at_utc": pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tables": {},
    }

    for table_name, frame in materialized.items():
        file_name = f"{table_name}.csv"
        frame.to_csv(snapshot_dir / file_name, index=False)
        manifest["tables"][table_name] = {
            "rows": int(len(frame)),
            "columns": list(frame.columns),
            "file": file_name,
        }

    (snapshot_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return snapshot_dir
