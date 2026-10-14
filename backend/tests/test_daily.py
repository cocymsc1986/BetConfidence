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


def test_pick_filters_probability_edge_and_gap(league_db, monkeypatch):
    from backend.model import markets

    model = {(p["market"], p["selection"], p["line"]): p["probability"]
             for p in markets.predict_match(db.get_match(league_db))["predictions"] if not p["player"]}
    home = model[("match_result", "home", None)]
    over = model[("total_goals", "over", 2.5)]
    # Margin-free prices: bookies 5 points below the model on goals (kept), 30 below on the home win (dropped).
    def two_way(market, line, p_first, sels):
        return [{"bookmaker": "bet365", "market": market, "selection": sel, "line": line, "price": 1 / q}
                for sel, q in zip(sels, (p_first, 1 - p_first))]
    quotes = two_way("total_goals", 2.5, over - 0.05, ("over", "under"))
    rest = 1 - (home - 0.30)
    quotes += [{"bookmaker": "bet365", "market": "match_result", "selection": sel, "line": None, "price": 1 / q}
               for sel, q in (("home", home - 0.30), ("draw", rest / 2), ("away", rest / 2))]
    odds.store_quotes(league_db, "api-football", quotes)
    day = db.get_match(league_db)["date"][:10]

    loose = daily.pick(day, top=10, min_prob=0.5, max_gap=1.0)
    picks = daily.pick(day, top=10, min_prob=0.5)
    assert {p["bet"] for p in picks} < {p["bet"] for p in loose}  # the home win is screened out
    assert [p["market"] for p in picks] == ["total_goals"]
    assert all(p["probability"] > 0.5 and p["edge"] > 0 for p in picks)
    assert all(p["probability"] - p["market_prob"] <= daily.MAX_GAP for p in picks)
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
