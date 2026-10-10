"""Free-tier daily run: bulk odds by date, a daily call budget, and the pick filter."""
import httpx

from backend import daily, db, ingest, odds
from backend.tests.test_odds_value import AF_RESPONSE
from backend.tests import test_refresh_flow
from backend.tests.test_refresh_flow import HISTORY, UPCOMING, _players, _stats

calls: list[str] = []


def handler(request: httpx.Request) -> httpx.Response:
    path, q = request.url.path, dict(request.url.params)
    calls.append(path)
    headers = {"x-ratelimit-requests-remaining": "95"}
    if path == "/fixtures" and "league" in q:
        return httpx.Response(200, headers=headers, json={"errors": [], "response": [UPCOMING, *HISTORY[1], *HISTORY[2]]})
    if path == "/fixtures/statistics":
        return httpx.Response(200, headers=headers, json={"errors": [], "response": _stats(int(q["fixture"]))})
    if path == "/fixtures/players":
        return httpx.Response(200, headers=headers, json={"errors": [], "response": _players(int(q["fixture"]))})
    if path == "/odds":
        assert "date" in q and "fixture" not in q
        items = [{**AF_RESPONSE[0], "fixture": {"id": 1}}, {**AF_RESPONSE[0], "fixture": {"id": 999}}]
        return httpx.Response(200, headers=headers, json={"errors": [], "paging": {"current": 1, "total": 1}, "response": items})
    return test_refresh_flow.handler(request)  # per-fixture refresh: /fixtures?id|team, lineups


def _mock(monkeypatch, budget):
    real_client = httpx.Client
    monkeypatch.setattr(ingest.httpx, "Client", lambda *a, **k: real_client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(ingest, "API_KEY", "test")
    monkeypatch.setattr(ingest, "MIN_INTERVAL", 0)
    monkeypatch.setattr(ingest, "DAILY_BUDGET", budget)
    monkeypatch.setattr(ingest, "_remaining", None)
    monkeypatch.setattr(odds, "ODDS_KEY", "")


def test_daily_run_stays_within_budget(tmp_db, monkeypatch):
    # 2 fixture seasons + 1 bulk odds call leaves 7 of 10: three matches backfilled (2 calls each).
    _mock(monkeypatch, budget=10)
    calls.clear()
    today = UPCOMING["fixture"]["date"][:10]
    out = daily.run(today)

    assert len(calls) <= 10
    assert calls.count("/odds") == 1
    r = out["refresh"]
    assert r["api_football_odds"]["fixtures"] == 1 and r["api_football_odds"]["unknown_fixtures"] == 1
    assert r["stats_backfill"]["enriched"] == 3
    assert r["stats_backfill"]["backlog"] == 7
    assert r["api_football_calls_left"] == 1
    assert db.api_calls_today("api-football") == len(calls)

    # Next call today is refused before it reaches the network.
    calls.clear()
    monkeypatch.setattr(ingest, "DAILY_BUDGET", db.api_calls_today("api-football"))
    assert ingest.enrich_backlog()["enriched"] == 0
    assert calls == []


def test_quota_header_caps_calls(tmp_db, monkeypatch):
    _mock(monkeypatch, budget=0)
    monkeypatch.setattr(ingest, "_remaining", 0)
    calls.clear()
    assert ingest.enrich_backlog()["enriched"] == 0
    assert calls == []


def test_pick_filters_probability_and_edge(league_db, monkeypatch):
    # Long prices on everything for the upcoming match: some bets will show edge.
    quotes = [
        {"bookmaker": "bet365", "market": "match_result", "selection": s, "line": None, "price": pr}
        for s, pr in (("home", 2.5), ("draw", 6.0), ("away", 12.0))
    ] + [
        {"bookmaker": "bet365", "market": "total_goals", "selection": s, "line": 2.5, "price": 2.4} for s in ("over", "under")
    ]
    odds.store_quotes(league_db, "api-football", quotes)
    day = db.get_match(league_db)["date"][:10]
    picks = daily.pick(day, top=10, min_prob=0.5)
    assert picks, "expected at least one pick"
    assert all(p["probability"] > 0.5 and p["edge"] > 0 for p in picks)
    assert [p["value_score"] for p in picks] == sorted((p["value_score"] for p in picks), reverse=True)
    assert "Paper picks" in daily._table({"date": day, "fixtures": 1, "picks": picks, "refresh": None})


def test_daily_run_on_pro_refreshes_each_fixture(tmp_db, monkeypatch):
    _mock(monkeypatch, budget=0)
    calls.clear()
    out = daily.run(UPCOMING["fixture"]["date"][:10])
    r = out["refresh"]
    assert set(r["per_fixture"]) == {1}
    assert "/fixtures/lineups" in calls
    assert r["stats_backfill"]["backlog"] == 0
    assert calls.count("/fixtures/statistics") == 10  # each history match fetched once, not again by the backfill
