"""Over 3.5 cards (match-level)."""
from __future__ import annotations

import math

from .. import db


def _team_foul_rate(team_id: int, n: int = 10) -> tuple[float, int]:
    """Average fouls per match this team has committed (across players)."""
    with db.get_conn() as conn:
        rows = list(
            conn.execute(
                """
                SELECT m.id, COALESCE(SUM(s.fouls_committed), 0) AS fouls
                FROM matches m
                JOIN player_match_stats s ON s.match_id = m.id
                JOIN players p ON p.id = s.player_id
                WHERE p.team_id = ? AND m.status = 'FT'
                GROUP BY m.id
                ORDER BY m.date DESC
                LIMIT ?
                """,
                (team_id, n),
            )
        )
    if not rows:
        return 0.0, 0
    return sum(int(r["fouls"]) for r in rows) / len(rows), len(rows)


def over_3_5_cards(match_id: int, home_team: int, away_team: int, referee_id: int | None) -> dict | None:
    ref_rate = db.referee_card_rate(referee_id) if referee_id else 0.0
    home_fouls, hn = _team_foul_rate(home_team)
    away_fouls, an = _team_foul_rate(away_team)
    if hn < 3 or an < 3:
        return None

    foul_signal = (home_fouls + away_fouls) / 22.0  # ~1.0 if both teams hit ~11 fouls/match
    expected_cards = ref_rate * (0.85 + 0.3 * foul_signal)
    if expected_cards <= 0:
        return None
    lam = expected_cards
    cum = sum(math.exp(-lam) * lam ** k / math.factorial(k) for k in range(0, 4))
    prob = max(0.0, min(1 - cum, 0.95))
    return {
        "market": "Over 3.5 Cards",
        "match_id": match_id,
        "confidence": round(prob, 3),
        "stat_summary": {
            "referee_avg_cards": round(ref_rate, 2),
            "home_team_avg_fouls": round(home_fouls, 2),
            "away_team_avg_fouls": round(away_fouls, 2),
            "expected_cards": round(expected_cards, 2),
        },
    }
