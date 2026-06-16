"""Both Teams To Score and Over 2.5 Goals signals (match-level)."""
from __future__ import annotations

from .. import db


def _btts_rate(team_id: int, n: int = 10) -> tuple[float, int]:
    matches = db.team_last_n_matches(team_id, n)
    if not matches:
        return 0.0, 0
    hits = sum(
        1 for m in matches
        if (m["home_goals"] or 0) > 0 and (m["away_goals"] or 0) > 0
    )
    return hits / len(matches), len(matches)


def _avg_goals(team_id: int, n: int = 10, *, home_only: bool = False, away_only: bool = False) -> tuple[float, float, int]:
    matches = db.team_last_n_matches(team_id, n, home_only=home_only, away_only=away_only)
    if not matches:
        return 0.0, 0.0, 0
    scored = []
    conceded = []
    for m in matches:
        if m["home_team"] == team_id:
            scored.append(m["home_goals"] or 0)
            conceded.append(m["away_goals"] or 0)
        else:
            scored.append(m["away_goals"] or 0)
            conceded.append(m["home_goals"] or 0)
    return sum(scored) / len(scored), sum(conceded) / len(conceded), len(matches)


def btts(match_id: int, home_team: int, away_team: int) -> dict | None:
    home_rate, hn = _btts_rate(home_team)
    away_rate, an = _btts_rate(away_team)
    if hn < 4 or an < 4:
        return None
    confidence = (home_rate + away_rate) / 2
    return {
        "market": "BTTS Yes",
        "match_id": match_id,
        "confidence": round(max(0.0, min(confidence, 0.95)), 3),
        "stat_summary": {
            "home_btts_rate_last_10": round(home_rate, 3),
            "away_btts_rate_last_10": round(away_rate, 3),
            "home_sample": hn,
            "away_sample": an,
        },
    }


def over_2_5(match_id: int, home_team: int, away_team: int) -> dict | None:
    h_scored, h_conceded, h_n = _avg_goals(home_team, home_only=True)
    a_scored, a_conceded, a_n = _avg_goals(away_team, away_only=True)
    if h_n < 4 or a_n < 4:
        return None
    expected = (h_scored + a_conceded) / 2 + (a_scored + h_conceded) / 2
    # Map expected goals to a confidence via a simple Poisson-style heuristic.
    # P(>2.5) ≈ 1 - exp(-λ) * (1 + λ + λ²/2)
    import math
    lam = max(0.1, expected)
    prob = 1 - math.exp(-lam) * (1 + lam + lam ** 2 / 2)
    return {
        "market": "Over 2.5 Goals",
        "match_id": match_id,
        "confidence": round(max(0.0, min(prob, 0.95)), 3),
        "stat_summary": {
            "expected_goals": round(expected, 2),
            "home_scored_avg": round(h_scored, 2),
            "home_conceded_avg": round(h_conceded, 2),
            "away_scored_avg": round(a_scored, 2),
            "away_conceded_avg": round(a_conceded, 2),
        },
    }
