"""One-click match analysis: refresh data, model every market, rank value bets."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import db, ingest, odds, value
from .model import markets

_CATEGORY_ORDER = {"result": 0, "goals": 1, "corners": 2, "cards": 3, "player": 4}


def analyse_match(match_id: int, *, refresh: bool = False, top: int = 10) -> dict[str, Any] | None:
    match = db.get_match(match_id)
    if match is None:
        return None

    refresh_report: dict[str, Any] | None = None
    if refresh:
        refresh_report = {
            "stats": ingest.refresh_for_match(match_id),
            "odds": odds.refresh_odds_for_match(match_id),
        }
        match = db.get_match(match_id)  # referee / kickoff may have changed

    model = markets.predict_match(match)
    quotes = db.odds_for_match(match_id)
    priced = value.price_predictions(model["predictions"], quotes)
    best = value.top_value(priced, top)

    warnings = list(model["warnings"])
    if refresh_report:
        stats, od = refresh_report["stats"], refresh_report["odds"]
        if not stats.get("ok"):
            warnings.append(f"Stats refresh failed: {stats.get('error') or '; '.join(stats.get('errors', []))[:300]}")
        elif stats.get("errors"):
            warnings.append(f"Stats refresh partly failed ({len(stats['errors'])} errors): {stats['errors'][0][:200]}")
        for name, label in (("api_football", "API-Football odds"), ("the_odds_api", "The Odds API")):
            res = od.get(name) or {}
            if res and not res.get("ok"):
                warnings.append(f"{label}: {res.get('error')}")
    if not quotes:
        warnings.append("No bookmaker odds cached for this match — showing model probabilities and fair odds only.")
    elif not best:
        warnings.append("No selection currently beats the bookmakers' prices by enough to be value.")
    newest = max((q["updated_at"] for q in quotes), default=None)

    return {
        "match": {
            "id": match["id"],
            "home": match["home_name"],
            "away": match["away_name"],
            "date": match["date"],
            "league": match["league"],
            "referee": match["referee_name"],
            "status": match["status"],
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "odds_updated_at": newest,
        "bookmakers": sorted({q["bookmaker"] for q in quotes}),
        "top_bets": best,
        "markets": sorted(
            priced,
            key=lambda p: (_CATEGORY_ORDER.get(p["category"], 9), p["market"], p["player"] or "", p["line"] or 0, p["selection"]),
        ),
        "context": model["context"],
        "warnings": warnings,
        "refresh": refresh_report,
    }
