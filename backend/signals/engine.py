"""Signals feed for the Signals / Player tabs, backed by the match models in backend/model.

Each signal keeps the original shape: {market, confidence, odds, ev_score,
stat_summary, match_context, player_name, team_name}.
"""
from __future__ import annotations

from typing import Any

from .. import db, value
from ..model import markets


def _signal(p: dict[str, Any], match, team_names: dict[int, str]) -> dict[str, Any]:
    return {
        "market": p["label"] if not p["player"] else p["label"].replace(p["player"], "").strip(),
        "market_key": p["market"],
        "selection": p["selection"],
        "line": p["line"],
        "match_id": match["id"],
        "player_id": p["player_id"],
        "player_name": p["player"],
        "team_name": team_names.get(p["team_id"]) if p["team_id"] else None,
        "confidence": p["probability"],
        "fair_odds": p["fair_odds"],
        "odds": p["best_odds"],
        "bookmaker": p["bookmaker"],
        "ev_score": p["ev"],
        "value_score": p["value_score"],
        "stat_summary": p["detail"],
        "match_context": {
            "match_id": match["id"],
            "home_team": match["home_team"],
            "away_team": match["away_team"],
            "home_name": match["home_name"],
            "away_name": match["away_name"],
            "date": match["date"],
            "referee": match["referee_name"],
        },
    }


def _sort_key(s: dict[str, Any]) -> tuple:
    has_ev = s.get("value_score") is not None
    return (0 if has_ev else 1, -(s.get("value_score") or 0.0), -(s.get("confidence") or 0.0))


def signals_for_matches(matches, player_id: int | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in matches:
        model = markets.predict_match(m)
        preds = model["predictions"]
        if player_id is not None:
            preds = [p for p in preds if p["player_id"] == player_id]
        priced = value.price_predictions(preds, db.odds_for_match(m["id"]))
        names = {m["home_team"]: m["home_name"], m["away_team"]: m["away_name"]}
        # Keep the feed readable: only the "over/yes" side of each market, and only
        # selections that are reasonably likely or priced.
        for p in priced:
            if p["selection"] in ("under", "no") and p["value_score"] is None:
                continue
            if p["probability"] < 0.3 and p["value_score"] is None:
                continue
            out.append(_signal(p, m, names))
    out.sort(key=_sort_key)
    return out


def signals_for_date(date_str: str) -> list[dict[str, Any]]:
    return signals_for_matches(db.matches_on(date_str))


def signals_for_player(player_id: int) -> list[dict[str, Any]]:
    player = db.get_player(player_id)
    if player is None or player["team_id"] is None:
        return []
    upcoming = [
        m for m in db.upcoming_matches(days=14)
        if player["team_id"] in (m["home_team"], m["away_team"])
    ][:3]
    return signals_for_matches(upcoming, player_id=player_id)
