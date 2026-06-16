"""Yellow card signal.

Confidence = booking rate last 10 (65/35 recency split) × referee card modifier × position modifier.
"""
from __future__ import annotations

from .. import db


POSITION_BOOKING_MOD = {
    "G": 0.4,   # goalkeepers booked rarely
    "D": 1.15,  # defenders booked more
    "M": 1.05,
    "F": 0.85,
}

# League-wide baseline used to normalise the referee modifier.
REF_BASELINE_CARDS = 3.5


def _recency_split(rows: list, key: str) -> float:
    """Apply a 65/35 weight split between the most recent half and the older half."""
    if not rows:
        return 0.0
    half = max(1, len(rows) // 2)
    recent = rows[:half]
    older = rows[half:]
    recent_rate = sum(int(r[key]) for r in recent) / max(1, len(recent))
    older_rate = sum(int(r[key]) for r in older) / max(1, len(older)) if older else recent_rate
    return 0.65 * recent_rate + 0.35 * older_rate


def calculate(player_id: int, match_id: int, referee_id: int | None) -> dict | None:
    rows = db.player_last_n_stats(player_id, n=10)
    if len(rows) < 4:
        return None

    base_rate = _recency_split(rows, "yellow_card")
    if base_rate <= 0:
        return None

    ref_rate = db.referee_card_rate(referee_id) if referee_id else REF_BASELINE_CARDS
    ref_mod = (ref_rate / REF_BASELINE_CARDS) if REF_BASELINE_CARDS else 1.0
    ref_mod = max(0.7, min(ref_mod, 1.4))

    position = rows[0]["position"] or ""
    pos_mod = POSITION_BOOKING_MOD.get(position[:1].upper(), 1.0)

    confidence = base_rate * ref_mod * pos_mod
    confidence = max(0.0, min(confidence, 0.95))

    return {
        "market": "Player Yellow Card",
        "player_id": player_id,
        "match_id": match_id,
        "confidence": round(confidence, 3),
        "stat_summary": {
            "base_rate_last_10": round(base_rate, 3),
            "referee_card_rate": round(ref_rate, 2),
            "referee_modifier": round(ref_mod, 2),
            "position": position,
            "position_modifier": pos_mod,
            "sample_size": len(rows),
        },
    }
