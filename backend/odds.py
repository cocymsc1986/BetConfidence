"""The Odds API integration. Pulls match & player markets and caches them in SQLite."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import httpx

from . import db


ODDS_HOST = "https://api.the-odds-api.com/v4"
ODDS_KEY = os.getenv("ODDS_API_KEY", "")

# The Odds API uses sport_keys, mapped from our TARGET_LEAGUES.
SPORT_KEYS = {
    39: "soccer_epl",
    140: "soccer_spain_la_liga",
    135: "soccer_italy_serie_a",
    78: "soccer_germany_bundesliga",
    61: "soccer_france_ligue_one",
}

TARGET_LEAGUES = [int(x) for x in os.getenv("TARGET_LEAGUES", "39").split(",") if x.strip()]

# Markets supported by The Odds API used by Pitch Edge.
MATCH_MARKETS = ["h2h", "totals", "btts", "alternate_totals_corners"]


def _cache(conn, match_id: int, market: str, selection: str, odds: float, bookmaker: str) -> None:
    db.upsert(
        conn,
        "odds_cache",
        {
            "match_id": match_id,
            "market": market,
            "selection": selection,
            "odds": odds,
            "bookmaker": bookmaker,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        keys=("match_id", "market", "selection"),
    )


def _match_odds_for_sport(client: httpx.Client, sport_key: str) -> list[dict[str, Any]]:
    resp = client.get(
        f"{ODDS_HOST}/sports/{sport_key}/odds",
        params={
            "apiKey": ODDS_KEY,
            "regions": "uk,eu",
            "markets": ",".join(MATCH_MARKETS),
            "oddsFormat": "decimal",
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


def _match_id_from_teams(conn, home_name: str, away_name: str) -> int | None:
    row = conn.execute(
        """
        SELECT m.id FROM matches m
        JOIN teams ht ON ht.id = m.home_team
        JOIN teams at ON at.id = m.away_team
        WHERE LOWER(ht.name) = LOWER(?) AND LOWER(at.name) = LOWER(?)
          AND date(m.date) >= date('now', '-1 day')
        ORDER BY m.date ASC LIMIT 1
        """,
        (home_name, away_name),
    ).fetchone()
    return int(row["id"]) if row else None


def refresh_match_odds() -> dict[str, Any]:
    if not ODDS_KEY:
        return {"ok": False, "error": "ODDS_API_KEY not set"}

    db.init_db()
    pulled = 0
    skipped = 0
    with httpx.Client() as client, db.get_conn() as conn:
        for league_id in TARGET_LEAGUES:
            sport_key = SPORT_KEYS.get(league_id)
            if not sport_key:
                continue
            try:
                events = _match_odds_for_sport(client, sport_key)
            except httpx.HTTPError:
                continue
            for ev in events:
                home = ev.get("home_team")
                away = ev.get("away_team")
                if not home or not away:
                    continue
                match_id = _match_id_from_teams(conn, home, away)
                if not match_id:
                    skipped += 1
                    continue
                for book in ev.get("bookmakers", []):
                    bname = book.get("key", "?")
                    for mkt in book.get("markets", []):
                        market_key = mkt.get("key")
                        for out in mkt.get("outcomes", []):
                            sel_parts = [str(out.get("name", "?"))]
                            if "point" in out:
                                sel_parts.append(str(out["point"]))
                            selection = "|".join(sel_parts)
                            price = out.get("price")
                            if price is None:
                                continue
                            _cache(conn, match_id, market_key, selection, float(price), bname)
                            pulled += 1
    return {"ok": True, "odds_rows": pulled, "skipped_events": skipped}
