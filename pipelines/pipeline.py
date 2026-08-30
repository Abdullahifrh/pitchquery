import argparse
import pandas as pd

from pipelines.utils import print_frame_summary
from pipelines.snapshot import export_csv_snapshot
from pipelines.transform.teams import build_dim_teams
from pipelines.transform.players import build_dim_players, build_bridge_player_seasons
from pipelines.transform.seasons import build_dim_seasons
from pipelines.transform.fixtures import (
    build_dim_fixtures,
    build_fact_match_events,
    build_fact_shot_events,
)
from pipelines.transform.stats import (
    build_fact_match_lineup,
    build_fact_player_fixture_stats,
    build_fact_team_match_stats,
    build_fact_player_season_stats,
    build_fact_team_season_stats,
    build_fact_premier_league_table,
)
from pipelines.extract.fpl import fetch_fpl_teams

def run_pipeline(season_id: int) -> dict[str, pd.DataFrame]:
    """
    Build the full ETL pipeline for a single season and return every
    intermediate/final dataframe in a stable dictionary.
    """

    print("\nBuilding dim_seasons...")
    dim_seasons = build_dim_seasons(season_id)
    print("\nBuilding dim_teams...")
    dim_teams = build_dim_teams(fetch_fpl_teams())
    print("\nBuilding dim_players...")
    dim_players = build_dim_players(season_id, dim_teams)
    print("\nBuilding dim_fixtures...")
    dim_fixtures = build_dim_fixtures(season_id, dim_teams)

    print("\nBuilding fact_match_events...")
    fact_match_events = build_fact_match_events(dim_fixtures, dim_teams, dim_players)
    print("\nBuilding fact_shot_events...")
    fact_shot_events = build_fact_shot_events(dim_fixtures, dim_teams, dim_players)

    print("\nBuilding bridge_player_seasons...")
    bridge_player_seasons = build_bridge_player_seasons(
        dim_players=dim_players,
        fact_match_events=fact_match_events,
        fact_shot_events=fact_shot_events,
        dim_fixtures=dim_fixtures,
        dim_teams=dim_teams,
    )

    print("\nBuilding fact_match_lineup...")
    fact_match_lineup = build_fact_match_lineup(
        dim_players=dim_players,
        bridge_player_seasons=bridge_player_seasons,
        dim_fixtures=dim_fixtures,
    )

    print("\nBuilding fact_player_fixture_stats...")
    fact_player_fixture_stats = build_fact_player_fixture_stats(
        fact_match_lineup=fact_match_lineup,
        dim_players=dim_players,
        dim_fixtures=dim_fixtures,
        fact_match_events=fact_match_events,
    )

    print("\nBuilding fact_team_match_stats...")
    fact_team_match_stats = build_fact_team_match_stats(
        dim_fixtures=dim_fixtures,
        fact_player_fixture_stats=fact_player_fixture_stats,
    )

    print("\nBuilding fact_player_season_stats...")
    fact_player_season_stats = build_fact_player_season_stats(
        dim_players=dim_players,
        fact_player_fixture_stats=fact_player_fixture_stats,
        season_id=season_id,
    )

    print("\nBuilding fact_team_season_stats...")
    fact_team_season_stats = build_fact_team_season_stats(
        fact_team_match_stats=fact_team_match_stats,
    )

    print("\nBuilding fact_premier_league_table...")
    fact_premier_league_table = build_fact_premier_league_table(
        fact_team_match_stats=fact_team_match_stats,
    )

    return {
        "dim_seasons": dim_seasons,
        "dim_teams": dim_teams,
        "dim_players": dim_players,
        "dim_fixtures": dim_fixtures,
        "fact_match_events": fact_match_events,
        "fact_shot_events": fact_shot_events,
        "bridge_player_seasons": bridge_player_seasons,
        "fact_match_lineup": fact_match_lineup,
        "fact_team_match_stats": fact_team_match_stats,
        "fact_player_season_stats": fact_player_season_stats,
        "fact_team_season_stats": fact_team_season_stats,
        "fact_premier_league_table": fact_premier_league_table,
    }

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the season ETL pipeline smoke test.")
    parser.add_argument(
        "--season-id",
        type=int,
        default=777,
        help="Pulse competition season id to build.",
    )
    parser.add_argument(
        "--export-snapshots",
        action="store_true",
        help="Write a versioned CSV snapshot after the pipeline finishes.",
    )
    parser.add_argument(
        "--snapshot-base-dir",
        type=str,
        default="data/snapshots",
        help="Base directory for CSV snapshot runs.",
    )
    args = parser.parse_args(argv)

    frames = run_pipeline(args.season_id)

    print(f"Pipeline completed for season_id={args.season_id}")
    for name in [
        "dim_seasons",
        "dim_teams",
        "dim_players",
        "dim_fixtures",
        "fact_match_events",
        "fact_shot_events",
        "bridge_player_seasons",
        "fact_match_lineup",
        "fact_team_match_stats",
        "fact_player_season_stats",
        "fact_team_season_stats",
        "fact_premier_league_table",
    ]:
        print_frame_summary(name, frames[name])

    if args.export_snapshots:
        snapshot_dir = export_csv_snapshot(
            frames=frames,
            season_id=args.season_id,
            base_dir=args.snapshot_base_dir,
        )
        print(f"Snapshot written to {snapshot_dir}")

if __name__ == "__main__":
    main()
