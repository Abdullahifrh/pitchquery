import pandas as pd

from pipelines.extract.pulse import fetch_pulse_season_name
from pipelines.utils import parse_season_label
from pipelines.schema import DEFAULT_COMPETITION_NAME

def build_dim_seasons(season_id: int) -> pd.DataFrame:
    """Build the dim_seasons DataFrame for one season_id."""
    raw_label = fetch_pulse_season_name(season_id)
    season_name = parse_season_label(raw_label)

    return pd.DataFrame(
        [
            {
                "season_id": int(season_id),
                "season_name": season_name,
                "competition_name": DEFAULT_COMPETITION_NAME,
            }
        ]
    )
