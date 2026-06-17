"""API-Football ingestion via RapidAPI / api-sports."""
from __future__ import annotations

import logging
import os
import time
from datetime import date, timedelta
from typing import Any

import httpx

from . import db


log = logging.getLogger("pitch_edge.ingest")
if not log.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("[ingest] %(message)s"))
    log.addHandler(h)
log.setLevel(logging.INFO)


API_HOST = os.getenv("API_FOOTBALL_HOST", "v3.football.api-sports.io")
API_KEY = os.getenv("API_FOOTBALL_KEY", "")
TARGET_LEAGUES = [int(x) for x in os.getenv("TARGET_LEAGUES", "39").split(",") if x.strip()]


# International tournaments (cups) use the tournament year as the season,
# not the Aug-May convention. Known IDs from api-sports.io:
#   1  FIFA World Cup
#   4  UEFA Euro Championship
#   5  UEFA Nations League
#   9  Copa America
#   13 CONMEBOL Libertadores
#   15 FIFA Club World Cup
# Domestic leagues use Aug-May. Anything not in this set is treated as Aug-May.
INTERNATIONAL_LEAGUES: set[int] = {1, 4, 5, 9, 13, 15}


def _active_season(today: date | None = None) -> int:
    """Pick the season currently being played (or just-ended) for an Aug-May league."""
    today = today or date.today()
    return today.year if today.month >= 8 else today.year - 1


def _seasons_for_league(league_id: int, today: date | None = None) -> list[int]:
    """Return the season values to query for a given league.

    Internationals (World Cup, Euros, Nations League, Copa America, etc.)
    use the tournament's calendar year. Club leagues use the active Aug-May
    season plus the prior one so the rolling 12-month window is covered.
    """
    today = today or date.today()
    if league_id in INTERNATIONAL_LEAGUES:
        # Pull current year and last year; covers an in-progress tournament
        # plus any qualifiers / Nations League matches from the prior cycle.
        return [today.year, today.year - 1]
    active = _active_season(today)
    return [active, active - 1]


DEFAULT_SEASON = int(os.getenv("API_FOOTBALL_SEASON", _active_season()))


def _headers() -> dict[str, str]:
    # Works for both api-sports.io and RapidAPI gateways.
    if "rapidapi" in API_HOST:
        return {
            "x-rapidapi-key": API_KEY,
            "x-rapidapi-host": API_HOST,
        }
    return {"x-apisports-key": API_KEY}


class APIFootballError(RuntimeError):
    """Raised when API-Football returns a body-level error envelope."""


def _get(client: httpx.Client, path: str, params: dict[str, Any]) -> dict[str, Any]:
    resp = client.get(f"https://{API_HOST}{path}", params=params, headers=_headers(), timeout=30.0)
    resp.raise_for_status()
    body = resp.json()
    # API-Football returns HTTP 200 even when something is wrong (bad key, quota,
    # invalid params). The "errors" field carries those messages.
    errors = body.get("errors")
    if errors and (isinstance(errors, dict) and errors or isinstance(errors, list) and errors):
        raise APIFootballError(f"{path} {params} -> errors={errors}")
    return body


def _last_12_months_range() -> tuple[str, str]:
    today = date.today()
    return ((today - timedelta(days=365)).isoformat(), today.isoformat())


def _upsert_team(conn, t: dict[str, Any], league: int) -> None:
    if not t or not t.get("id"):
        return
    db.upsert(
        conn,
        "teams",
        {
            "id": t["id"],
            "name": t.get("name", "Unknown"),
            "league": league,
            "country": t.get("country"),
        },
        keys=("id",),
    )


def _upsert_referee(conn, name: str | None) -> int | None:
    if not name:
        return None
    cur = conn.execute("SELECT id FROM referees WHERE name = ?", (name,))
    row = cur.fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute("INSERT INTO referees(name) VALUES (?)", (name,))
    return int(cur.lastrowid)


def _fixture_to_match_row(fx: dict[str, Any], referee_id: int | None) -> dict[str, Any]:
    """Row for the initial fixture upsert.

    Note: we deliberately omit home_corners / away_corners here so that a
    subsequent refresh doesn't wipe the values we filled in from
    /fixtures/statistics. Those columns default to NULL on first insert and
    are written by the stats pass via a direct UPDATE.
    """
    info = fx["fixture"]
    teams = fx["teams"]
    goals = fx.get("goals", {}) or {}
    return {
        "id": info["id"],
        "home_team": teams["home"]["id"],
        "away_team": teams["away"]["id"],
        "date": info["date"],
        "league": fx["league"]["id"],
        "referee_id": referee_id,
        "home_goals": goals.get("home"),
        "away_goals": goals.get("away"),
        "status": info.get("status", {}).get("short"),
    }


def _persist_fixture_stats(conn, fixture_id: int, fixture_stats: list[dict[str, Any]]) -> tuple[int | None, int | None]:
    """Returns (home_corners, away_corners) from team statistics block."""
    home_corners = away_corners = None
    if not fixture_stats:
        return home_corners, away_corners
    # First team listed in statistics is home in API-Football's response.
    for idx, team_block in enumerate(fixture_stats):
        for s in team_block.get("statistics", []):
            if s.get("type") == "Corner Kicks":
                val = s.get("value")
                if val is None:
                    continue
                if idx == 0:
                    home_corners = int(val)
                else:
                    away_corners = int(val)
    return home_corners, away_corners


def _persist_player_stats(conn, fixture_id: int, players_payload: list[dict[str, Any]]) -> None:
    for team_block in players_payload:
        team_id = team_block.get("team", {}).get("id")
        for pblk in team_block.get("players", []):
            p = pblk.get("player", {})
            stats_list = pblk.get("statistics") or []
            if not stats_list:
                continue
            s = stats_list[0]
            games = s.get("games", {}) or {}
            shots = s.get("shots", {}) or {}
            goals = s.get("goals", {}) or {}
            passes = s.get("passes", {}) or {}
            cards = s.get("cards", {}) or {}
            fouls = s.get("fouls", {}) or {}

            db.upsert(
                conn,
                "players",
                {
                    "id": p["id"],
                    "name": p.get("name", "Unknown"),
                    "team_id": team_id,
                    "position": games.get("position"),
                    "nationality": None,
                    "age": None,
                },
                keys=("id",),
            )
            db.upsert(
                conn,
                "player_match_stats",
                {
                    "player_id": p["id"],
                    "match_id": fixture_id,
                    "goals": goals.get("total") or 0,
                    "assists": passes.get("assists") or goals.get("assists") or 0,
                    "shots_total": shots.get("total") or 0,
                    "shots_on_target": shots.get("on") or 0,
                    "yellow_card": cards.get("yellow") or 0,
                    "red_card": cards.get("red") or 0,
                    "fouls_committed": fouls.get("committed") or 0,
                    "fouls_drawn": fouls.get("drawn") or 0,
                    "minutes_played": games.get("minutes") or 0,
                    "position": games.get("position"),
                },
                keys=("player_id", "match_id"),
            )


def refresh(
    *,
    leagues: list[int] | None = None,
    seasons: list[int] | None = None,
    days_back: int = 365,
    days_forward: int = 7,
    fetch_stats: bool = True,
    max_stats_per_league: int = 200,
) -> dict[str, Any]:
    """Pull fixtures + per-player stats for the configured leagues.

    By default we fetch both the active season and the prior one so the rolling
    12-month window stays covered around the off-season changeover.
    """
    if not API_KEY:
        return {"ok": False, "error": "API_FOOTBALL_KEY not set — check .env"}

    db.init_db()
    leagues = leagues or TARGET_LEAGUES

    today = date.today()
    from_date = (today - timedelta(days=days_back)).isoformat()
    to_date = (today + timedelta(days=days_forward)).isoformat()

    per_league: dict[int, dict[str, Any]] = {}
    errors: list[str] = []
    totals = {"fixtures": 0, "stats_pulled": 0}

    log.info("refresh start leagues=%s window=%s..%s", leagues, from_date, to_date)

    with httpx.Client() as client:
        for league_id in leagues:
            # Internationals (World Cup etc.) use the tournament year; club
            # leagues use the Aug-May active season + prior. Override with
            # `seasons` if the caller knows better.
            league_seasons = seasons if seasons is not None else _seasons_for_league(league_id, today)
            league_stats = {
                "fixtures": 0,
                "stats_pulled": 0,
                "kind": "international" if league_id in INTERNATIONAL_LEAGUES else "club",
                "seasons_queried": [],
            }
            for season in league_seasons:
                try:
                    fx_resp = _get(
                        client,
                        "/fixtures",
                        {"league": league_id, "season": season, "from": from_date, "to": to_date},
                    )
                except (httpx.HTTPError, APIFootballError) as e:
                    msg = f"league {league_id} season {season}: {e}"
                    log.warning(msg)
                    errors.append(msg)
                    continue

                fixtures = fx_resp.get("response", [])
                league_stats["seasons_queried"].append({"season": season, "fixtures": len(fixtures)})
                log.info("league=%s season=%s returned %d fixtures", league_id, season, len(fixtures))

                if not fixtures:
                    continue

                with db.get_conn() as conn:
                    for fx in fixtures:
                        _upsert_team(conn, fx["teams"]["home"], league_id)
                        _upsert_team(conn, fx["teams"]["away"], league_id)
                        ref_id = _upsert_referee(conn, fx["fixture"].get("referee"))
                        db.upsert(conn, "matches", _fixture_to_match_row(fx, ref_id), keys=("id",))
                        league_stats["fixtures"] += 1

            if fetch_stats:
                # Only pull per-player stats for finished fixtures we haven't enriched yet.
                with db.get_conn() as conn:
                    finished_ids = [
                        int(r["id"])
                        for r in conn.execute(
                            "SELECT id FROM matches WHERE league = ? AND status = 'FT' "
                            "AND id NOT IN (SELECT DISTINCT match_id FROM player_match_stats) "
                            "ORDER BY date DESC LIMIT ?",
                            (league_id, max_stats_per_league),
                        )
                    ]
                log.info("league=%s enriching %d finished fixtures", league_id, len(finished_ids))

                for fx_id in finished_ids:
                    try:
                        pls = _get(client, "/fixtures/players", {"fixture": fx_id}).get("response", [])
                        fxs = _get(client, "/fixtures/statistics", {"fixture": fx_id}).get("response", [])
                    except (httpx.HTTPError, APIFootballError) as e:
                        msg = f"fixture {fx_id}: {e}"
                        log.warning(msg)
                        errors.append(msg)
                        time.sleep(0.5)
                        continue

                    with db.get_conn() as conn:
                        home_c, away_c = _persist_fixture_stats(conn, fx_id, fxs)
                        if home_c is not None or away_c is not None:
                            conn.execute(
                                "UPDATE matches SET home_corners=?, away_corners=? WHERE id=?",
                                (home_c, away_c, fx_id),
                            )
                        _persist_player_stats(conn, fx_id, pls)
                    league_stats["stats_pulled"] += 1
                    time.sleep(0.2)  # gentle rate-limit

            per_league[league_id] = league_stats
            totals["fixtures"] += league_stats["fixtures"]
            totals["stats_pulled"] += league_stats["stats_pulled"]

    ok = totals["fixtures"] > 0 or not errors
    return {
        "ok": ok,
        "seasons_requested": seasons,  # None = auto per-league
        "window": {"from": from_date, "to": to_date},
        "totals": totals,
        "by_league": per_league,
        "errors": errors,
    }


def discover_leagues(query: str) -> dict[str, Any]:
    """Hit /leagues?search=… so the user can find unknown competition IDs."""
    if not API_KEY:
        return {"ok": False, "error": "API_FOOTBALL_KEY not set"}
    with httpx.Client() as client:
        try:
            resp = _get(client, "/leagues", {"search": query})
        except (httpx.HTTPError, APIFootballError) as e:
            return {"ok": False, "error": str(e)}
    results = []
    for item in resp.get("response", []):
        lg = item.get("league", {}) or {}
        ctry = item.get("country", {}) or {}
        seasons = [s.get("year") for s in item.get("seasons", []) if s.get("year")]
        results.append({
            "id": lg.get("id"),
            "name": lg.get("name"),
            "type": lg.get("type"),  # "League" or "Cup"
            "country": ctry.get("name"),
            "seasons": seasons[-5:],  # last few only
        })
    return {"ok": True, "count": len(results), "results": results}
