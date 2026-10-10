"""Drives the one-click refresh against a mocked API-Football (no network)."""
from datetime import datetime, timedelta, timezone

import httpx

from backend import db, ingest, odds
from backend.tests.test_odds_value import AF_RESPONSE

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _fixture(fid, home, away, days, status="FT", hg=1, ag=1, referee="Anthony Taylor, England"):
    return {
        "fixture": {"id": fid, "date": (NOW + timedelta(days=days)).isoformat(), "referee": referee,
                    "status": {"short": status}},
        "league": {"id": 39},
        "teams": {"home": {"id": home, "name": f"Club {home}"}, "away": {"id": away, "name": f"Club {away}"}},
        "goals": {"home": hg if status == "FT" else None, "away": ag if status == "FT" else None},
        "score": {"fulltime": {"home": hg, "away": ag}},
    }


# Each team's last 5 matches (alternating opponents 3..7), all finished.
HISTORY = {
    t: [_fixture(t * 100 + i, t if i % 2 else 3 + i, 3 + i if i % 2 else t, -(i * 4 + 2), hg=i % 3, ag=1) for i in range(5)]
    for t in (1, 2)
}
UPCOMING = _fixture(1, 1, 2, 1, status="NS")


def _stats(fid):
    home, away = (f for f in [*HISTORY[1], *HISTORY[2]] if f["fixture"]["id"] == fid).__next__()["teams"].values()
    return [
        {"team": {"id": t["id"]}, "statistics": [
            {"type": "Corner Kicks", "value": 6}, {"type": "Total Shots", "value": 12},
            {"type": "Shots on Goal", "value": 4}, {"type": "Fouls", "value": 10},
            {"type": "Yellow Cards", "value": 2}, {"type": "Red Cards", "value": None},
            {"type": "Ball Possession", "value": "50%"}]}
        for t in (away, home)  # deliberately away first
    ]


def _players(fid):
    match = next(f for f in [*HISTORY[1], *HISTORY[2]] if f["fixture"]["id"] == fid)
    out = []
    for side in ("home", "away"):
        tid = match["teams"][side]["id"]
        out.append({"team": {"id": tid}, "players": [
            {"player": {"id": tid * 1000 + k, "name": f"A. Player{chr(97 + k)}"},
             "statistics": [{"games": {"minutes": 90 if k < 11 else None, "position": "F" if k > 8 else "M",
                                       "substitute": k >= 11},
                             "shots": {"total": 3 if k == 9 else None, "on": 1 if k == 9 else None},
                             "goals": {"total": None, "assists": None},
                             "cards": {"yellow": 0, "red": 0}, "fouls": {"committed": 1, "drawn": 1}}]}
            for k in range(14)
        ]})
    return out


def handler(request: httpx.Request) -> httpx.Response:
    path, q = request.url.path, dict(request.url.params)
    calls.append(path)
    if path == "/fixtures" and "id" in q:
        return httpx.Response(200, json={"errors": [], "response": [UPCOMING]})
    if path == "/fixtures" and "team" in q:
        return httpx.Response(200, json={"errors": [], "response": HISTORY[int(q["team"])]})
    if path == "/fixtures/statistics":
        return httpx.Response(200, json={"errors": [], "response": _stats(int(q["fixture"]))})
    if path == "/fixtures/players":
        return httpx.Response(200, json={"errors": [], "response": _players(int(q["fixture"]))})
    if path == "/fixtures/lineups":
        return httpx.Response(200, json={"errors": [], "response": []})
    if path == "/odds":
        return httpx.Response(200, json={"errors": [], "paging": {"current": 1, "total": 1}, "response": AF_RESPONSE})
    return httpx.Response(404)


calls: list[str] = []


def test_one_click_refresh(tmp_db, monkeypatch):
    real_client = httpx.Client
    monkeypatch.setattr(ingest.httpx, "Client", lambda *a, **k: real_client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(ingest, "API_KEY", "test")
    monkeypatch.setattr(ingest, "MIN_INTERVAL", 0)
    monkeypatch.setattr(odds, "ODDS_KEY", "")
    with db.get_conn() as conn:
        ingest._store_fixtures(conn, [UPCOMING])

    from fastapi.testclient import TestClient
    from backend.main import app

    calls.clear()
    body = TestClient(app).post("/matches/1/analyse?top=10").json()
    assert body["refresh"]["stats"]["ok"], body["refresh"]
    assert body["refresh"]["odds"]["api_football"]["quotes"] > 0
    assert body["context"]["home_matches_used"] == 5
    assert body["match"]["referee"] == "Anthony Taylor"  # ", England" suffix stripped
    assert any(w.startswith("The Odds API") for w in body["warnings"])
    assert body["markets"]

    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM team_match_stats").fetchone()[0] == 20  # 10 matches x 2 sides
        subs = conn.execute("SELECT COUNT(*) FROM player_match_stats WHERE substitute = 1").fetchone()[0]
        assert subs == 10 * 2 * 3

    # Second click: history already enriched -> no stats/players calls.
    calls.clear()
    TestClient(app).post("/matches/1/analyse")
    assert "/fixtures/statistics" not in calls and "/fixtures/players" not in calls
