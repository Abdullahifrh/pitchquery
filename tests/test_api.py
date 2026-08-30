import pytest

# Skips this whole module cleanly if fastapi isn't installed, matching
# every other DB/framework-dependent fixture's skip-not-fail pattern.
pytest.importorskip("fastapi.testclient")

def test_health_returns_200_with_normalized_season_name(client):
    response = client.get("/health")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert body["db_ready"] is True
    assert body["latest_season_id"] == 841
    assert body["latest_season_name"] == "2026/27"
    assert set(body["tables"].keys()) == {"dim_seasons", "dim_teams", "dim_players", "dim_fixtures"}

def test_fixtures_endpoints_return_200_with_expected_shape(client):
    listing = client.get("/api/v1/fixtures/", params={"season_id": 841})
    assert listing.status_code == 200
    body = listing.json()
    assert isinstance(body, list) and body
    assert body[0]["fixture_id"] == 100
    assert body[0]["season_id"] == 841

    detail = client.get("/api/v1/fixtures/100")
    assert detail.status_code == 200
    assert detail.json()["fixture_id"] == 100

    assert client.get("/api/v1/fixtures/999999").status_code == 404

def test_standings_table_returns_200_ordered_by_points(client):
    """Maps to the task's `/table` endpoint — the actual route is
    `/api/v1/teams/standings` (see api/routers/teams.py); there is no
    literal `/table` path in this API."""
    response = client.get("/api/v1/teams/standings", params={"season_id": 841})
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list) and len(body) >= 2
    assert body[0]["points"] >= body[1]["points"]

def test_players_endpoints_return_200_with_expected_shape(client):
    listing = client.get("/api/v1/players/")
    assert listing.status_code == 200
    body = listing.json()
    assert isinstance(body, list) and body
    assert body[0]["player_id"] == 10

    detail = client.get("/api/v1/players/10")
    assert detail.status_code == 200
    assert detail.json()["player_id"] == 10

    assert client.get("/api/v1/players/999999").status_code == 404
