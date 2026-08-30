import pandas as pd
from pipelines.extract.pulse import fetch_pulse_teams

def build_dim_teams(teams: dict) -> pd.DataFrame:
    rows = []
    for team in teams:
        fpl_id = team.get("id")
        pulse_id = team.get("pulse_id")
        code = team.get("code")
        team_logo_url = None
        if code is not None:
            team_logo_url = f"https://resources.premierleague.com/premierleague/badges/50/t{code}.png"

        # Retrieve PulseLive data for the teams
        pulse_teams = fetch_pulse_teams(int(pulse_id))
        # Retrieve PulseLive names for teams
        team_name = pulse_teams.get("name", team.get("name"))
        club = pulse_teams.get("club", {})
        short_name = club.get("abbr", team.get("short_name"))
        grounds_lst = pulse_teams.get("grounds", [])
        primary_ground = grounds_lst[0] if grounds_lst else {}

        stadium = primary_ground.get("name")
        city = primary_ground.get("city")

        rows.append(
            {
                "team_id": int(pulse_id),
                "fpl_team_id": int(fpl_id),
                "team_name": str(team_name).strip(),
                "short_name": str(short_name).strip(),
                "stadium": str(stadium).strip() if stadium else None,
                "city": str(city).strip() if city else None,
                "team_logo_url": team_logo_url
            }
        )
    
    df = pd.DataFrame(rows)
    df = df.dropna(subset=["team_id"]).drop_duplicates(subset=["team_id"]).copy()
    df["team_id"] = df["team_id"].astype(int)

    cols = ["team_id", "fpl_team_id", "team_name", "short_name", "stadium", "city", "team_logo_url"]
    print(f"Final dim_teams created. Resolved {len(df)} teams.")
    return df[cols]
