import os
import re
import time

import pytest
from sqlalchemy import text

from rag.db import get_db_engine, open_connection
from rag.engine import ask

os.environ.setdefault("RAG_VERBOSE_TURNS", "true")

pytestmark = [
    pytest.mark.live_rag,
    pytest.mark.skipif(
        not os.getenv("GEMINI_API_KEY"),
        reason="GEMINI_API_KEY not set - this file makes real Gemini calls",
    ),
    pytest.mark.skipif(
        not all(os.getenv(var) for var in ("DB_USER", "DB_PASSWORD", "DB_HOST", "DB_NAME")),
        reason="DB_USER/DB_PASSWORD/DB_HOST/DB_NAME not set - this file queries the real Neon database",
    ),
]

REJECTION_PHRASES = (
    "rejected this request",
    "couldn't complete this request",
    "couldn't reach a final answer",
)
LEAKED_IDENTIFIER_TERMS = ("player_id", "team_id", "fixture_id", "season_id")

BETWEEN_QUESTION_PAUSE_SECONDS = 5

@pytest.fixture(scope="module")
def db_conn():
    engine = get_db_engine()
    with open_connection(engine) as conn:
        yield conn

@pytest.fixture(scope="module")
def current_season_id(db_conn):
    # Mirrors schema_context.py's own definition of "current" exactly, so
    # the ground truth here always targets the same season the model will.
    row = db_conn.execute(
        text(
            "SELECT s.season_id FROM warehouse.dim_seasons s "
            "WHERE EXISTS (SELECT 1 FROM warehouse.dim_fixtures f "
            "WHERE f.season_id = s.season_id AND f.fixture_status IN ('U', 'L'))"
        )
    ).first()
    if row is None:
        pytest.skip("no season with fixtures remaining found - is the warehouse up to date?")
    return row.season_id

@pytest.fixture(autouse=True)
def _pause_between_questions():
    yield
    time.sleep(BETWEEN_QUESTION_PAUSE_SECONDS)

def _ask_and_report(question: str):
    result = ask(question)
    print(f"\nQ: {question}")
    print(f"A: {result.answer}")
    print(f"SQL:\n{result.sql}")
    return result

def _assert_no_leaked_identifiers(answer: str):
    lowered = answer.lower()
    for term in LEAKED_IDENTIFIER_TERMS:
        assert term not in lowered, f"raw identifier '{term}' leaked into the answer: {answer}"

def _assert_answered_successfully(result):
    assert result.answer, "empty answer"
    lowered = result.answer.lower()
    for phrase in REJECTION_PHRASES:
        assert phrase not in lowered, f"the engine did not produce a real answer: {result.answer}"
    assert result.sql, f"expected at least one query to have run, got none: {result.answer}"
    _assert_no_leaked_identifiers(result.answer)

def _extract_numbers(answer: str) -> list[float]:
    return [float(match.replace(",", "")) for match in re.findall(r"\d[\d,]*\.?\d*", answer)]

def _assert_contains_number_close_to(answer: str, expected: float, tolerance: float):
    numbers = _extract_numbers(answer)
    assert numbers, f"no number found in the answer to compare against the expected {expected}: {answer}"
    assert any(abs(n - expected) <= tolerance for n in numbers), (
        f"expected a number within {tolerance} of {expected} in the answer, found {numbers}: {answer}"
    )

def _assert_numbers_contain_all(answer: str, expected_values: list[float], tolerance: float):
    numbers = _extract_numbers(answer)
    assert numbers, f"no numbers found in the answer to compare against {expected_values}: {answer}"
    for expected in expected_values:
        assert any(abs(n - expected) <= tolerance for n in numbers), (
            f"expected a number within {tolerance} of {expected} in the answer, found {numbers}: {answer}"
        )

def _resolve_player_id(conn, name_pattern: str) -> int:
    row = conn.execute(
        text("SELECT player_id FROM warehouse.dim_players WHERE player_name ILIKE :pattern LIMIT 1"),
        {"pattern": name_pattern},
    ).first()
    if row is None:
        pytest.skip(f"no player matching {name_pattern!r} found in warehouse.dim_players")
    return row.player_id

def _ground_truth_team_goals(conn, season_id: int, team_name: str) -> int:
    row = conn.execute(
        text(
            "SELECT p.goals_for FROM warehouse.fact_premier_league_table p "
            "JOIN warehouse.dim_teams t ON t.team_id = p.team_id "
            "WHERE t.team_name ILIKE :team_name AND p.season_id = :season_id"
        ),
        {"team_name": team_name, "season_id": season_id},
    ).first()
    if row is None:
        pytest.skip(f"no fact_premier_league_table row for {team_name!r} in season {season_id}")
    return row.goals_for

def _ground_truth_own_goals(conn, season_id: int):
    rows = conn.execute(
        text(
            "SELECT team_id, COUNT(*) AS og FROM warehouse.fact_match_events "
            "WHERE event_type = 'own goal' AND season_id = :season_id "
            "GROUP BY team_id"
        ),
        {"season_id": season_id},
    ).all()
    if not rows:
        pytest.skip(f"no own goals recorded for season {season_id}")
    total = sum(row.og for row in rows)
    max_og = max(row.og for row in rows)
    top_team_ids = [row.team_id for row in rows if row.og == max_og]
    top_team_names = {
        name for (name,) in conn.execute(
            text("SELECT team_name FROM warehouse.dim_teams WHERE team_id = ANY(:ids)"),
            {"ids": top_team_ids},
        ).all()
    }
    return total, top_team_names

def _ground_truth_top_yellow_card_players(conn, season_id: int):
    rows = conn.execute(
        text(
            "SELECT p.player_name, f.yellow_card FROM warehouse.fact_player_season_stats f "
            "JOIN warehouse.dim_players p ON f.player_id = p.player_id "
            "WHERE f.season_id = :season_id "
            "AND f.yellow_card = (SELECT MAX(yellow_card) FROM warehouse.fact_player_season_stats WHERE season_id = :season_id)"
        ),
        {"season_id": season_id},
    ).all()
    if not rows or not rows[0].yellow_card:
        pytest.skip(f"no yellow cards recorded yet for season {season_id}")
    return {row.player_name for row in rows}, rows[0].yellow_card

def _ground_truth_goals_per_90(conn, season_id: int, player_id: int) -> float:
    row = conn.execute(
        text(
            "SELECT goals, mins_played FROM warehouse.fact_player_season_stats "
            "WHERE season_id = :season_id AND player_id = :player_id"
        ),
        {"season_id": season_id, "player_id": player_id},
    ).first()
    if row is None or not row.mins_played:
        pytest.skip(f"no usable fact_player_season_stats row for player {player_id} in season {season_id}")
    return row.goals * 90.0 / row.mins_played

def _ground_truth_xgi_per_90(conn, season_id: int, player_id: int) -> float:
    row = conn.execute(
        text(
            "SELECT xg, xa, mins_played FROM warehouse.fact_player_season_stats "
            "WHERE season_id = :season_id AND player_id = :player_id"
        ),
        {"season_id": season_id, "player_id": player_id},
    ).first()
    if row is None or not row.mins_played:
        pytest.skip(f"no usable fact_player_season_stats row for player {player_id} in season {season_id}")
    return (row.xg + row.xa) * 90.0 / row.mins_played

def _ground_truth_set_piece_assist_leader(conn, season_id: int):
    # goal_assist_deadball is the canonical column for this definition -
    # see its dictionary comment. fact_shot_events(shot_type='Set Piece')
    # is printed for context only, since it measures something related
    # but distinct (the shot's own origin, not the assisting delivery).
    leader = conn.execute(
        text(
            "SELECT p.player_name, f.goal_assist_deadball FROM warehouse.fact_player_season_stats f "
            "JOIN warehouse.dim_players p ON f.player_id = p.player_id "
            "WHERE f.season_id = :season_id "
            "ORDER BY f.goal_assist_deadball DESC NULLS LAST LIMIT 1"
        ),
        {"season_id": season_id},
    ).first()
    for_context = conn.execute(
        text(
            "SELECT p.player_name, COUNT(*) AS set_piece_assists FROM warehouse.fact_shot_events e "
            "JOIN warehouse.dim_players p ON e.player2_id = p.player_id "
            "WHERE e.season_id = :season_id AND e.shot_type = 'Set Piece' "
            "GROUP BY p.player_name ORDER BY set_piece_assists DESC LIMIT 1"
        ),
        {"season_id": season_id},
    ).first()
    return leader, for_context

def _ground_truth_best_home_ppg(conn, season_id: int):
    rows = conn.execute(
        text(
            "SELECT t.team_name, p.home_points, p.home_matches_played "
            "FROM warehouse.fact_premier_league_table p "
            "JOIN warehouse.dim_teams t ON t.team_id = p.team_id "
            "WHERE p.season_id = :season_id AND p.home_matches_played > 0"
        ),
        {"season_id": season_id},
    ).all()
    if not rows:
        pytest.skip(f"no home-record data for season {season_id}")
    ratios = [(row.team_name, row.home_points / row.home_matches_played) for row in rows]
    best_ppg = max(ratio for _, ratio in ratios)
    leaders = {name for name, ratio in ratios if abs(ratio - best_ppg) < 1e-9}
    return leaders, best_ppg

def _ground_truth_table_leader(conn, season_id: int) -> str:
    return conn.execute(
        text(
            "SELECT t.team_name FROM warehouse.fact_premier_league_table p "
            "JOIN warehouse.dim_teams t ON t.team_id = p.team_id "
            "WHERE p.season_id = :season_id "
            "ORDER BY p.points DESC, p.goals_difference DESC, p.goals_for DESC LIMIT 1"
        ),
        {"season_id": season_id},
    ).scalar_one()

def test_p1_01_liverpool_goals_this_season(db_conn, current_season_id):
    expected = _ground_truth_team_goals(db_conn, current_season_id, "Liverpool")
    result = _ask_and_report("How many goals has Liverpool scored this season?")
    _assert_answered_successfully(result)
    assert "i assumed" not in result.answer.lower()
    _assert_contains_number_close_to(result.answer, expected, tolerance=0.5)

def test_p1_02_own_goals_and_beneficiary_this_season(db_conn, current_season_id):
    expected_total, expected_teams = _ground_truth_own_goals(db_conn, current_season_id)
    result = _ask_and_report(
        "How many own goals were scored this season, and which team benefited the most?"
    )
    _assert_answered_successfully(result)
    _assert_contains_number_close_to(result.answer, expected_total, tolerance=0.5)
    lowered = result.answer.lower()
    assert any(team.lower() in lowered for team in expected_teams), (
        f"expected one of {expected_teams} to be named as the beneficiary: {result.answer}"
    )

def test_p2_03_haaland_goals_per_90_this_season(db_conn, current_season_id):
    player_id = _resolve_player_id(db_conn, "%Haaland%")
    expected = _ground_truth_goals_per_90(db_conn, current_season_id, player_id)
    result = _ask_and_report("What is Erling Haaland's goals per 90 minutes this season?")
    _assert_answered_successfully(result)
    _assert_contains_number_close_to(result.answer, expected, tolerance=0.05)

def test_p2_04_most_yellow_cards_this_season_with_ties(db_conn, current_season_id):
    expected_names, expected_count = _ground_truth_top_yellow_card_players(db_conn, current_season_id)
    result = _ask_and_report(
        "Which player has the most yellow cards this season, and is anyone else tied?"
    )
    _assert_answered_successfully(result)
    _assert_contains_number_close_to(result.answer, expected_count, tolerance=0.5)
    lowered = result.answer.lower()
    named = {name for name in expected_names if name.lower() in lowered}
    assert named, f"expected at least one of {expected_names} to be named: {result.answer}"
    if len(expected_names) > 1:
        assert len(named) == len(expected_names), (
            f"expected all {len(expected_names)} tied players {expected_names} named, only found {named}: {result.answer}"
        )

def test_p2_05_haaland_vs_joao_pedro_xgi_per_90(db_conn, current_season_id):
    haaland_id = _resolve_player_id(db_conn, "%Haaland%")
    pedro_id = _resolve_player_id(db_conn, "%João Pedro%")
    expected_haaland = _ground_truth_xgi_per_90(db_conn, current_season_id, haaland_id)
    expected_pedro = _ground_truth_xgi_per_90(db_conn, current_season_id, pedro_id)
    result = _ask_and_report(
        "Compare Erling Haaland and João Pedro's expected goal involvement (xGI) per 90 this season."
    )
    _assert_answered_successfully(result)
    lowered = result.answer.lower()
    assert "haaland" in lowered
    assert "pedro" in lowered
    _assert_numbers_contain_all(result.answer, [expected_haaland, expected_pedro], tolerance=0.05)

def test_p2_06_most_assists_from_set_pieces_this_season(db_conn, current_season_id):
    leader, for_context = _ground_truth_set_piece_assist_leader(db_conn, current_season_id)
    print(
        "\n[ground truth] by fact_player_season_stats.goal_assist_deadball (canonical): "
        f"{leader}\n[for comparison only] by fact_shot_events(shot_type='Set Piece'): {for_context}"
    )
    if leader is None or not leader.goal_assist_deadball:
        pytest.skip("no set-piece assists recorded yet this season - nothing to verify against")
    result = _ask_and_report("Who has provided the most assists specifically from set pieces this season?")
    _assert_answered_successfully(result)
    assert leader.player_name.lower() in result.answer.lower(), (
        f"expected {leader.player_name!r} to be named: {result.answer}"
    )
    _assert_contains_number_close_to(result.answer, leader.goal_assist_deadball, tolerance=0.5)

def test_p2_07_best_home_record_points_per_game_this_season(db_conn, current_season_id):
    expected_teams, expected_ppg = _ground_truth_best_home_ppg(db_conn, current_season_id)
    result = _ask_and_report(
        "Which team has the best home record this season, by points per game at home?"
    )
    _assert_answered_successfully(result)
    lowered = result.answer.lower()
    assert any(team.lower() in lowered for team in expected_teams), (
        f"expected one of {expected_teams} (home PPG {expected_ppg:.2f}) to be named: {result.answer}"
    )

def test_p3_08_la_liga_top_scorer_out_of_scope():
    result = _ask_and_report("Who was the top scorer in La Liga this season?")
    assert result.answer
    assert result.sql is None

def test_p3_09_injury_prediction_out_of_scope():
    result = _ask_and_report("Which player is most likely to get injured next based on current form?")
    assert result.answer
    assert result.sql is None

@pytest.mark.extra
def test_p4_10_current_standings_table_format(db_conn, current_season_id):
    expected_leader = _ground_truth_table_leader(db_conn, current_season_id)
    result = _ask_and_report("Output the current 2026/27 Premier League table.")
    _assert_answered_successfully(result)
    lowered = result.answer.lower()
    assert expected_leader.lower() in lowered, f"expected table leader {expected_leader!r} to appear: {result.answer}"
    for unrequested_term in ("expected goal", "xga", "xgd"):
        assert unrequested_term not in lowered, f"answer included an unrequested xG-family metric: {result.answer}"
    assert not re.search(r"\bxg\b", lowered), f"answer included an unrequested xG metric: {result.answer}"

@pytest.mark.extra
def test_p4_11_shooting_profile_comparison_exploratory():
    result = _ask_and_report(
        "Compare the shooting profile between Erling Haaland and João Pedro from last season."
    )
    assert result.answer
    lowered = result.answer.lower()
    assert "haaland" in lowered
    assert "pedro" in lowered
