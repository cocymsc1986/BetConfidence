"""API-Football ingestion via RapidAPI / api-sports."""
from __future__ import annotations

import os
import time
from datetime import date, timedelta
from typing import Any

import httpx

from . import db


API_HOST = os.getenv("API_FOOTBALL_HOST", "v3.football.api-sports.io")
API_KEY = os.getenv("API_FOOTBALL_KEY", "")
TARGET_LEAGUES = [int(x) for x in os.getenv("TARGET_LEAGUES", "39").split(",") if x.strip()]
DEFAULT_SEASON = int(os.getenv("API_FOOTBALL_SEASON", date.today().year))


def _headers() -> dict[str, str]:
    # Works for both api-sports.io and RapidAPI gateways.
    if "rapidapi" in API_HOST:
        return {
            "x-rapidapi-key": API_KEY,
            "x-rapidapi-host": API_HOST,
        }
    return {"x-apisports-key": API_KEY}


def _get(client: httpx.Client, path: str, params: dict[str, Any]) -> dict[str, Any]:
    resp = client.get(f"https://{API_HOST}{path}", params=params, headers=_headers(), timeout=30.0)
    resp.raise_for_status()
    return resp.json()


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
    info = fx["fixture"]
    teams = fx["teams"]
    goals = fx.get("goals", {}) or {}
    score = fx.get("score", {}) or {}
    return {
        "id": info["id"],
        "home_team": teams["home"]["id"],
        "away_team": teams["away"]["id"],
        "date": info["date"],
        "league": fx["league"]["id"],
        "referee_id": referee_id,
        "home_corners": None,
        "away_corners": None,
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
    season: int = DEFAULT_SEASON,
    days_back: int = 365,
    days_forward: int = 7,
    fetch_stats: bool = True,
) -> dict[str, Any]:
    """Pull fixtures + per-player stats for the configured leagues."""
    if not API_KEY:
        return {"ok": False, "error": "API_FOOTBALL_KEY not set"}

    db.init_db()
    leagues = leagues or TARGET_LEAGUES
    today = date.today()
    from_date = (today - timedelta(days=days_back)).isoformat()
    to_date = (today + timedelta(days=days_forward)).isoformat()

    counts = {"fixtures": 0, "stats_pulled": 0, "errors": 0}

    with httpx.Client() as client:
        for league_id in leagues:
            try:
                fx_resp = _get(
                    client,
                    "/fixtures",
                    {"league": league_id, "season": season, "from": from_date, "to": to_date},
                )
            except httpx.HTTPError as e:
                counts["errors"] += 1
                continue

            fixtures = fx_resp.get("response", [])
            with db.get_conn() as conn:
                for fx in fixtures:
                    _upsert_team(conn, fx["teams"]["home"], league_id)
                    _upsert_team(conn, fx["teams"]["away"], league_id)
                    referee_name = fx["fixture"].get("referee")
                    ref_id = _upsert_referee(conn, referee_name)
                    row = _fixture_to_match_row(fx, ref_id)
                    db.upsert(conn, "matches", row, keys=("id",))
                    counts["fixtures"] += 1

            if not fetch_stats:
                continue

            # Only pull per-player stats for finished fixtures we haven't enriched yet.
            with db.get_conn() as conn:
                finished_ids = [
                    int(r["id"])
                    for r in conn.execute(
                        "SELECT id FROM matches WHERE league = ? AND status = 'FT' "
                        "AND id NOT IN (SELECT DISTINCT match_id FROM player_match_stats)",
                        (league_id,),
                    )
                ]

            for fx_id in finished_ids:
                try:
                    pls = _get(client, "/fixtures/players", {"fixture": fx_id}).get("response", [])
                    fxs = _get(client, "/fixtures/statistics", {"fixture": fx_id}).get("response", [])
                except httpx.HTTPError:
                    counts["errors"] += 1
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
                counts["stats_pulled"] += 1
                time.sleep(0.2)  # gentle rate-limit

    return {"ok": True, **counts}
