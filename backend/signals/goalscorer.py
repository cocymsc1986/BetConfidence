"""Anytime goalscorer and assist signals."""
from __future__ import annotations

from .. import db


POSITION_GOAL_MOD = {
    "G": 0.05,
    "D": 0.5,
    "M": 0.9,
    "F": 1.25,
}

POSITION_ASSIST_MOD = {
    "G": 0.05,
    "D": 0.7,
    "M": 1.2,
    "F": 1.0,
}


def _rate_last_n(rows, key: str) -> float:
    if not rows:
        return 0.0
    hits = sum(1 for r in rows if int(r[key] or 0) > 0)
    return hits / len(rows)


def goalscorer(player_id: int, match_id: int) -> dict | None:
    rows = db.player_last_n_stats(player_id, n=10)
    if len(rows) < 4:
        return None
    rate = _rate_last_n(rows, "goals")
    if rate <= 0:
        return None
    pos = rows[0]["position"] or ""
    mod = POSITION_GOAL_MOD.get(pos[:1].upper(), 1.0)
    confidence = max(0.0, min(rate * mod, 0.95))
    return {
        "market": "Anytime Goalscorer",
        "player_id": player_id,
        "match_id": match_id,
        "confidence": round(confidence, 3),
        "stat_summary": {
            "goal_rate_last_10": round(rate, 3),
            "position": pos,
            "position_modifier": mod,
            "sample_size": len(rows),
        },
    }


def assist(player_id: int, match_id: int) -> dict | None:
    rows = db.player_last_n_stats(player_id, n=10)
    if len(rows) < 4:
        return None
    rate = _rate_last_n(rows, "assists")
    if rate <= 0:
        return None
    pos = rows[0]["position"] or ""
    mod = POSITION_ASSIST_MOD.get(pos[:1].upper(), 1.0)
    confidence = max(0.0, min(rate * mod, 0.95))
    return {
        "market": "Anytime Assist",
        "player_id": player_id,
        "match_id": match_id,
        "confidence": round(confidence, 3),
        "stat_summary": {
            "assist_rate_last_10": round(rate, 3),
            "position": pos,
            "position_modifier": mod,
            "sample_size": len(rows),
        },
    }
