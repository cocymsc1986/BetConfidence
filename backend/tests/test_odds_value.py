from backend import db, ingest, odds, value
from backend.model import markets


AF_RESPONSE = [{
    "bookmakers": [
        {"id": 8, "name": "Bet365", "bets": [
            {"id": 1, "name": "Match Winner", "values": [
                {"value": "Home", "odd": "1.30"}, {"value": "Draw", "odd": "6.00"}, {"value": "Away", "odd": "10.00"}]},
            {"id": 5, "name": "Goals Over/Under", "values": [
                {"value": "Over 2.5", "odd": "1.50"}, {"value": "Under 2.5", "odd": "2.60"}]},
            {"id": 45, "name": "Corners Over Under", "values": [
                {"value": "Over 9.5", "odd": "2.40"}, {"value": "Under 9.5", "odd": "1.55"}]},
            {"id": 92, "name": "Anytime Goal Scorer", "values": [
                {"value": "Erling Haaland", "odd": "2.10"}]},
            {"id": 999, "name": "Some Exotic Market", "values": [{"value": "X", "odd": "3.0"}]},
        ]},
        {"id": 11, "name": "Pinnacle", "bets": [
            {"id": 45, "name": "Corners Over Under", "values": [
                {"value": "Over 9.5", "odd": "1.80"}, {"value": "Under 9.5", "odd": "2.05"}]},
        ]},
    ],
}]

TOA_EVENT = {
    "home_team": "Team 1", "away_team": "Team 10",
    "bookmakers": [{"key": "williamhill", "markets": [
        {"key": "h2h", "outcomes": [
            {"name": "Team 1", "price": 1.33}, {"name": "Team 10", "price": 9.0}, {"name": "Draw", "price": 5.5}]},
        {"key": "btts", "outcomes": [{"name": "Yes", "price": 1.9}, {"name": "No", "price": 1.85}]},
        {"key": "alternate_totals_corners", "outcomes": [
            {"name": "Over", "point": 10.5, "price": 2.2}, {"name": "Under", "point": 10.5, "price": 1.6}]},
        {"key": "player_shots_on_target", "outcomes": [
            {"name": "Over", "description": "E. Haaland", "point": 0.5, "price": 1.45}]},
        {"key": "player_assists", "outcomes": [
            {"name": "Over", "description": "Erling Haaland", "point": 0.5, "price": 9.0}]},
    ]}],
}


def test_normalise_api_football():
    quotes, unmapped = odds.normalise_api_football(AF_RESPONSE)
    keys = {(q["bookmaker"], q["market"], q["selection"], q["line"], q["player"]) for q in quotes}
    assert ("bet365", "match_result", "home", None, None) in keys
    assert ("bet365", "total_corners", "over", 9.5, None) in keys
    assert ("pinnacle", "total_corners", "under", 9.5, None) in keys
    assert ("bet365", "player_goal", "yes", None, "Erling Haaland") in keys
    assert unmapped == ["Some Exotic Market"]


def test_normalise_odds_api_event():
    quotes = odds.normalise_odds_api_event(TOA_EVENT)
    by = {(q["market"], q["selection"], q["line"]): q for q in quotes}
    assert by[("match_result", "home", None)]["price"] == 1.33
    assert by[("match_result", "away", None)]["price"] == 9.0
    assert by[("btts", "yes", None)]["price"] == 1.9
    assert by[("total_corners", "over", 10.5)]["price"] == 2.2
    assert by[("player_shots_on_target", "over", 0.5)]["player"] == "E. Haaland"
    assert by[("player_assist", "yes", None)]["price"] == 9.0


def test_bookmaker_and_name_matching():
    assert odds.bookmaker_key("Betfair_ex_uk") == "betfair_exchange"
    assert odds.bookmaker_key("William Hill") == odds.bookmaker_key("williamhill")
    assert odds.bookmaker_key("unibet_uk") == "unibet"
    assert odds.player_name_match("E. Haaland", "Erling Haaland")
    assert odds.player_name_match("Erling Braut Haaland", "Erling Haaland")
    assert odds.player_name_match("Rodri", "Rodri")
    assert not odds.player_name_match("B. Silva", "Bernardo Costa")
    assert not odds.player_name_match("J. Silva", "Bernardo Silva")
    assert odds._team_similarity("Manchester United", "Manchester United FC") == 1.0


def test_consensus_devig():
    quotes = [
        {"bookmaker": "a", "market": "btts", "selection": "yes", "line": None, "player": None, "price": 1.9},
        {"bookmaker": "a", "market": "btts", "selection": "no", "line": None, "player": None, "price": 1.9},
    ]
    probs = value._market_probs(quotes)
    assert abs(probs[("btts", "yes", None, None)][0] - 0.5) < 1e-9


def test_value_ranking_end_to_end(league_db):
    """A deliberately mispriced corners line should surface; fairly priced lines should not."""
    quotes, _ = odds.normalise_api_football(AF_RESPONSE)
    odds.store_quotes(league_db, "api-football", quotes)
    odds.store_quotes(league_db, "the-odds-api", odds.normalise_odds_api_event(TOA_EVENT))

    m = db.get_match(league_db)
    preds = markets.predict_match(m)["predictions"]
    priced = value.price_predictions(preds, db.odds_for_match(league_db))
    over95 = next(p for p in priced if p["market"] == "total_corners" and p["selection"] == "over" and p["line"] == 9.5)
    assert over95["best_odds"] == 2.40 and over95["bookmaker"] == "bet365"
    assert over95["market_books"] == 2  # bet365 + pinnacle both two-sided
    assert len(over95["prices"]) == 2

    # Bookmaker name "E. Haaland" resolved to our player.
    sot = next(p for p in priced if p["market"] == "player_shots_on_target" and p["line"] == 0.5
               and p["player"] == "Erling Haaland")
    assert sot["best_odds"] == 1.45

    top = value.top_value(priced, 10)
    assert all(t["ev"] > 0 and t["value_score"] > 0 for t in top)
    families = [(t["market"], t["selection"], t["player"]) for t in top]
    assert len(families) == len(set(families))  # no near-duplicate lines
    assert any(t["market"] == "total_corners" and t["selection"] == "over" for t in top)


def test_persist_fixture_stats_matches_team_by_id(tmp_db):
    with db.get_conn() as conn:
        conn.execute("INSERT INTO teams(id, name) VALUES (1,'H'),(2,'A')")
        conn.execute("INSERT INTO matches(id, home_team, away_team, date, league, status) VALUES (5,1,2,'2026-01-01',39,'FT')")
        # Away team's block listed FIRST — old code assumed index 0 was home.
        payload = [
            {"team": {"id": 2}, "statistics": [{"type": "Corner Kicks", "value": 3}, {"type": "Yellow Cards", "value": None},
                                                {"type": "Ball Possession", "value": "41%"}]},
            {"team": {"id": 1}, "statistics": [{"type": "Corner Kicks", "value": 9}, {"type": "Yellow Cards", "value": 2}]},
        ]
        ingest._persist_fixture_stats(conn, 5, payload)
        row = conn.execute("SELECT home_corners, away_corners FROM matches WHERE id=5").fetchone()
        assert (row["home_corners"], row["away_corners"]) == (9, 3)
        away = conn.execute("SELECT * FROM team_match_stats WHERE team_id=2").fetchone()
        assert away["yellow_cards"] == 0 and away["possession"] == 41


def test_extra_time_uses_90_minute_score():
    fx = {
        "fixture": {"id": 1, "date": "2026-01-01T15:00:00+00:00", "status": {"short": "AET"}},
        "league": {"id": 2},
        "teams": {"home": {"id": 1}, "away": {"id": 2}},
        "goals": {"home": 2, "away": 1},
        "score": {"fulltime": {"home": 1, "away": 1}},
    }
    row = ingest._fixture_to_match_row(fx, None)
    assert (row["home_goals"], row["away_goals"]) == (1, 1)


def test_analyse_endpoint(league_db):
    from fastapi.testclient import TestClient
    from backend.main import app

    quotes, _ = odds.normalise_api_football(AF_RESPONSE)
    odds.store_quotes(league_db, "api-football", quotes)
    client = TestClient(app)
    r = client.get(f"/matches/{league_db}/analysis?top=10")
    assert r.status_code == 200
    body = r.json()
    assert body["match"]["home"] == "Team 1"
    assert len(body["top_bets"]) <= 10
    assert body["markets"] and body["bookmakers"] == ["bet365", "pinnacle"]
    assert client.get("/matches/999999/analysis").status_code == 404
    # Old bug: /signals 500'd whenever a fixture existed on the date.
    date = db.get_match(league_db)["date"][:10]
    assert client.get(f"/signals?match_date={date}").status_code == 200
    assert client.get("/matches/upcoming?days=3").json()["matches"][0]["odds"]["home_win"] == 1.30


def test_api_football_error_envelope_raises_clean_error(monkeypatch):
    import httpx
    import pytest

    def handler(request):
        return httpx.Response(200, json={"errors": {"token": "Error/Missing application key."}, "response": []})

    monkeypatch.setattr(ingest, "MIN_INTERVAL", 0)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ingest.APIFootballError, match="Missing application key"):
            ingest._get(client, "/fixtures", {"id": 1})
