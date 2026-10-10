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


# Minimum seconds between API-Football calls. Free plan allows ~10 req/min;
# paid plans allow hundreds, so lower this if you're on Pro.
MIN_INTERVAL = float(os.getenv("API_FOOTBALL_MIN_INTERVAL", "0.25"))
_last_call = 0.0


def _get(client: httpx.Client, path: str, params: dict[str, Any]) -> dict[str, Any]:
    global _last_call
    wait = MIN_INTERVAL - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()
    resp = client.get(f"https://{API_HOST}{path}", params=params, headers=_headers(), timeout=30.0)
    resp.raise_for_status()
    body = resp.json()
    # API-Football returns HTTP 200 even when something is wrong (bad key, quota,
    # invalid params). The "errors" field carries those messages.
    errors = body.get("errors")
    if errors:  # {} / [] when fine
        raise APIFootballError(f"{path} {params} -> errors={errors}")
    return body


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
    # API-Football is inconsistent: "Michael Oliver" vs "Michael Oliver, England".
    name = (name or "").split(",")[0].strip()
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
    status = (info.get("status") or {}).get("short")
    goals = fx.get("goals", {}) or {}
    if status in ("AET", "PEN"):
        # `goals` includes extra time; markets settle on the 90-minute score.
        goals = ((fx.get("score") or {}).get("fulltime") or goals)
    return {
        "id": info["id"],
        "home_team": teams["home"]["id"],
        "away_team": teams["away"]["id"],
        "date": info["date"],
        "league": fx["league"]["id"],
        "referee_id": referee_id,
        "home_goals": goals.get("home"),
        "away_goals": goals.get("away"),
        "status": status,
    }


def _stat_int(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip().rstrip("%")
        if not v:
            return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


# API-Football /fixtures/statistics "type" -> team_match_stats column
_TEAM_STAT_TYPES = {
    "Corner Kicks": "corners",
    "Total Shots": "shots_total",
    "Shots on Goal": "shots_on_target",
    "Fouls": "fouls",
    "Yellow Cards": "yellow_cards",
    "Red Cards": "red_cards",
    "Ball Possession": "possession",
}
# For these, API-Football reports null rather than 0 when nothing happened.
_NULL_MEANS_ZERO = {"corners", "yellow_cards", "red_cards"}


def _persist_fixture_stats(conn, fixture_id: int, fixture_stats: list[dict[str, Any]]) -> int:
    """Write per-team stats for a fixture. Returns the number of team rows stored.

    Teams are matched by id: the order of blocks in the response is not guaranteed
    to be home-then-away, and relying on it silently swaps corners between sides.
    """
    stored = 0
    match = conn.execute("SELECT home_team, away_team FROM matches WHERE id = ?", (fixture_id,)).fetchone()
    for team_block in fixture_stats or []:
        team_id = (team_block.get("team") or {}).get("id")
        stats = team_block.get("statistics") or []
        if not team_id or not stats:
            continue
        row: dict[str, Any] = {"match_id": fixture_id, "team_id": team_id}
        for s in stats:
            col = _TEAM_STAT_TYPES.get(s.get("type"))
            if col:
                row[col] = _stat_int(s.get("value"))
        for col in _TEAM_STAT_TYPES.values():
            row.setdefault(col, None)
            if row[col] is None and col in _NULL_MEANS_ZERO:
                row[col] = 0
        db.upsert(conn, "team_match_stats", row, keys=("match_id", "team_id"))
        stored += 1
        if match is not None:
            side = "home" if team_id == match["home_team"] else "away" if team_id == match["away_team"] else None
            if side:
                conn.execute(f"UPDATE matches SET {side}_corners = ? WHERE id = ?", (row["corners"], fixture_id))
    return stored


def _persist_player_stats(conn, fixture_id: int, players_payload: list[dict[str, Any]]) -> None:
    for team_block in players_payload:
        team_id = team_block.get("team", {}).get("id")
        for pblk in team_block.get("players", []):
            p = pblk.get("player", {})
            stats_list = pblk.get("statistics") or []
            if not stats_list or not p.get("id"):
                continue
            s = stats_list[0]
            games = s.get("games", {}) or {}
            shots = s.get("shots", {}) or {}
            goals = s.get("goals", {}) or {}
            cards = s.get("cards", {}) or {}
            fouls = s.get("fouls", {}) or {}

            conn.execute(
                """
                INSERT INTO players (id, name, team_id, position) VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    position = COALESCE(excluded.position, players.position),
                    -- only move a player to this team if this is their most recent match
                    team_id = CASE WHEN ? >= COALESCE((
                        SELECT MAX(m.date) FROM player_match_stats s
                        JOIN matches m ON m.id = s.match_id WHERE s.player_id = ?
                    ), '') THEN excluded.team_id ELSE players.team_id END
                """,
                (
                    p["id"],
                    p.get("name", "Unknown"),
                    team_id,
                    games.get("position"),
                    _fixture_date(conn, fixture_id),
                    p["id"],
                ),
            )
            db.upsert(
                conn,
                "player_match_stats",
                {
                    "player_id": p["id"],
                    "match_id": fixture_id,
                    "team_id": team_id,
                    "substitute": 1 if games.get("substitute") else 0,
                    "goals": goals.get("total") or 0,
                    # API-Football reports assists under goals.assists.
                    "assists": goals.get("assists") or 0,
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


def _fixture_date(conn, fixture_id: int) -> str:
    row = conn.execute("SELECT date FROM matches WHERE id = ?", (fixture_id,)).fetchone()
    return row["date"] if row else ""


def _store_fixtures(conn, fixtures: list[dict[str, Any]], league_hint: int | None = None) -> int:
    for fx in fixtures:
        league_id = (fx.get("league") or {}).get("id") or league_hint
        _upsert_team(conn, fx["teams"]["home"], league_id)
        _upsert_team(conn, fx["teams"]["away"], league_id)
        ref_id = _upsert_referee(conn, fx["fixture"].get("referee"))
        db.upsert(conn, "matches", _fixture_to_match_row(fx, ref_id), keys=("id",))
    return len(fixtures)


def _enrich_fixtures(client: httpx.Client, fixture_ids: list[int], errors: list[str]) -> int:
    """Pull /fixtures/statistics + /fixtures/players for each finished fixture. Returns count enriched."""
    done = 0
    for fx_id in fixture_ids:
        try:
            fxs = _get(client, "/fixtures/statistics", {"fixture": fx_id}).get("response", [])
            pls = _get(client, "/fixtures/players", {"fixture": fx_id}).get("response", [])
        except (httpx.HTTPError, APIFootballError) as e:
            msg = f"fixture {fx_id}: {e}"
            log.warning(msg)
            errors.append(msg)
            continue
        with db.get_conn() as conn:
            _persist_fixture_stats(conn, fx_id, fxs)
            _persist_player_stats(conn, fx_id, pls)
        done += 1
    return done


def _unenriched_finished(conn, where: str, params: tuple, limit: int) -> list[int]:
    return [
        int(r["id"])
        for r in conn.execute(
            f"SELECT id FROM matches WHERE {where} AND status IN {db._FINISHED_SQL} "
            "AND id NOT IN (SELECT match_id FROM team_match_stats) "
            "ORDER BY date DESC LIMIT ?",
            (*params, limit),
        )
    ]


def refresh(
    *,
    leagues: list[int] | None = None,
    seasons: list[int] | None = None,
    days_back: int = 365,
    days_forward: int = 7,
    fetch_stats: bool = True,
    max_stats_per_league: int = 200,
) -> dict[str, Any]:
    """Pull fixtures + per-team/per-player stats for the configured leagues.

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
                if fixtures:
                    with db.get_conn() as conn:
                        league_stats["fixtures"] += _store_fixtures(conn, fixtures, league_id)

            if fetch_stats:
                with db.get_conn() as conn:
                    finished_ids = _unenriched_finished(conn, "league = ?", (league_id,), max_stats_per_league)
                log.info("league=%s enriching %d finished fixtures", league_id, len(finished_ids))
                league_stats["stats_pulled"] = _enrich_fixtures(client, finished_ids, errors)

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


HISTORY_MATCHES = int(os.getenv("HISTORY_MATCHES", "20"))


def refresh_for_match(match_id: int, *, history: int | None = None) -> dict[str, Any]:
    """Bring everything needed to analyse one fixture up to date.

    - re-reads the fixture itself (status, referee appointment)
    - pulls each team's last `history` finished matches in *any* competition
      (league, cups, Europe, internationals) and enriches any without stats
    - pulls confirmed lineups when published (~20-40 min before kick-off)

    Costs roughly 4 + 2 x (new finished matches) API-Football calls; once a
    team's history is cached, a re-run costs ~4 calls.
    """
    if not API_KEY:
        return {"ok": False, "error": "API_FOOTBALL_KEY not set — check .env"}
    history = history or HISTORY_MATCHES
    db.init_db()
    m = db.get_match(match_id)
    if m is None:
        return {"ok": False, "error": f"match {match_id} not in database — run /ingest/refresh first"}

    errors: list[str] = []
    out: dict[str, Any] = {"ok": True, "match_id": match_id, "teams": {}, "lineups": False, "errors": errors}
    with httpx.Client() as client:
        try:
            fx = _get(client, "/fixtures", {"id": match_id}).get("response", [])
            with db.get_conn() as conn:
                _store_fixtures(conn, fx)
        except (httpx.HTTPError, APIFootballError) as e:
            errors.append(f"fixture {match_id}: {e}")

        for side in ("home_team", "away_team"):
            team_id = int(m[side])
            try:
                fx = _get(client, "/fixtures", {"team": team_id, "last": history}).get("response", [])
            except (httpx.HTTPError, APIFootballError) as e:
                errors.append(f"team {team_id} history: {e}")
                continue
            with db.get_conn() as conn:
                _store_fixtures(conn, fx)
                ids = [int(f["fixture"]["id"]) for f in fx]
                marks = ",".join("?" for _ in ids) or "NULL"
                todo = _unenriched_finished(conn, f"id IN ({marks})", tuple(ids), history)
            enriched = _enrich_fixtures(client, todo, errors)
            out["teams"][team_id] = {"history_fixtures": len(fx), "newly_enriched": enriched}

        try:
            lineups = _get(client, "/fixtures/lineups", {"fixture": match_id}).get("response", [])
            if lineups:
                with db.get_conn() as conn:
                    _persist_lineups(conn, match_id, lineups)
                out["lineups"] = True
        except (httpx.HTTPError, APIFootballError) as e:
            errors.append(f"lineups {match_id}: {e}")
    out["ok"] = not errors or bool(out["teams"])
    return out


def _persist_lineups(conn, match_id: int, lineups: list[dict[str, Any]]) -> None:
    conn.execute("DELETE FROM lineups WHERE match_id = ?", (match_id,))
    for block in lineups:
        team_id = (block.get("team") or {}).get("id")
        for key, starter in (("startXI", 1), ("substitutes", 0)):
            for entry in block.get(key) or []:
                pid = (entry.get("player") or {}).get("id")
                if pid and team_id:
                    conn.execute(
                        "INSERT OR REPLACE INTO lineups(match_id, team_id, player_id, starter) VALUES (?,?,?,?)",
                        (match_id, team_id, pid, starter),
                    )


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
