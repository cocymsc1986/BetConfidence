"""Over 9.5 corners signal."""
from __future__ import annotations

import math

from .. import db


def _team_corners(team_id: int, *, home_only: bool, away_only: bool, n: int = 10) -> tuple[float, int]:
    matches = db.team_last_n_matches(team_id, n, home_only=home_only, away_only=away_only)
    totals: list[int] = []
    for m in matches:
        # Total corners *in the match* — we're predicting an over 9.5 on the combined number.
        if m["home_corners"] is None or m["away_corners"] is None:
            continue
        totals.append((m["home_corners"] or 0) + (m["away_corners"] or 0))
    if not totals:
        return 0.0, 0
    return sum(totals) / len(totals), len(totals)


def over_9_5(match_id: int, home_team: int, away_team: int) -> dict | None:
    home_avg, hn = _team_corners(home_team, home_only=True, away_only=False)
    away_avg, an = _team_corners(away_team, home_only=False, away_only=True)
    if hn < 3 or an < 3:
        return None
    expected = (home_avg + away_avg) / 2
    if expected <= 0:
        return None
    # Poisson tail above 9.5 with λ = expected total corners.
    lam = expected
    cum = sum(math.exp(-lam) * lam ** k / math.factorial(k) for k in range(0, 10))
    prob = max(0.0, min(1 - cum, 0.95))
    return {
        "market": "Over 9.5 Corners",
        "match_id": match_id,
        "confidence": round(prob, 3),
        "stat_summary": {
            "home_avg_total_corners_home_games": round(home_avg, 2),
            "away_avg_total_corners_away_games": round(away_avg, 2),
            "expected_total_corners": round(expected, 2),
            "home_sample": hn,
            "away_sample": an,
        },
    }
