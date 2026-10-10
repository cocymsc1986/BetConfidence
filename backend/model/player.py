"""Player prop model.

For each player likely to feature:
  * per-90 rates for shots, shots on target, goals, assists, cards — recency
    weighted over their last appearances (any team / competition) and shrunk
    toward a position prior so a 2-game sample can't produce a 90% prop;
  * a minutes model: P(start), P(sub appearance), typical minutes in each case,
    from the team's recent matches (overridden by confirmed lineups);
  * opponent / venue / referee adjustments from the team model.

Probabilities are *conditional on the player appearing*, because that's how
bookmakers settle player props (non-runners are void).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .. import db
from .dist import clamp_prob, count_pmf, poisson_pmf, recency_weights

STATS = ("shots_total", "shots_on_target", "goals", "assists", "cards")

# Per-90 priors for top-flight football by position (G/D/M/F). Used for shrinkage.
POSITION_PRIOR_90: dict[str, dict[str, float]] = {
    "G": {"shots_total": 0.01, "shots_on_target": 0.0, "goals": 0.0, "assists": 0.005, "cards": 0.06},
    "D": {"shots_total": 0.65, "shots_on_target": 0.18, "goals": 0.04, "assists": 0.06, "cards": 0.18},
    "M": {"shots_total": 1.20, "shots_on_target": 0.38, "goals": 0.10, "assists": 0.13, "cards": 0.19},
    "F": {"shots_total": 2.40, "shots_on_target": 0.90, "goals": 0.36, "assists": 0.14, "cards": 0.12},
}
PRIOR_MINUTES = 360.0      # shrinkage strength: 4 full matches of "average player at this position"
PLAYER_HALF_LIFE = 10.0    # appearances
TEAM_WINDOW = 8            # team matches used for the minutes model
SHOTS_VAR_FACTOR = 1.25    # player shot counts are mildly over-dispersed


@dataclass
class PlayerOutlook:
    player_id: int
    name: str
    team_id: int
    position: str
    p_start: float
    p_sub: float
    mins_start: float
    mins_sub: float
    rates90: dict[str, float]
    minutes_sample: float
    apps_sample: int
    lineup_status: str | None  # "starting" / "bench" / None if lineups not out

    @property
    def p_play(self) -> float:
        return min(self.p_start + self.p_sub, 1.0)

    def scenarios(self) -> list[tuple[float, float]]:
        """(probability, minutes) conditional on playing."""
        play = self.p_play
        if play <= 0:
            return []
        out = []
        if self.p_start > 0:
            out.append((self.p_start / play, self.mins_start))
        if self.p_sub > 0:
            out.append((self.p_sub / play, self.mins_sub))
        return out

    def prob_at_least(self, stat: str, k: int, multiplier: float = 1.0, var_factor: float = 1.0) -> float:
        rate = self.rates90[stat] * multiplier
        total = 0.0
        for p, mins in self.scenarios():
            lam = rate * mins / 90.0
            cdf = sum(count_pmf(i, lam, var_factor) if var_factor > 1 else poisson_pmf(i, lam) for i in range(k))
            total += p * (1.0 - cdf)
        return clamp_prob(total)

    def expected(self, stat: str, multiplier: float = 1.0) -> float:
        return sum(p * self.rates90[stat] * multiplier * m / 90.0 for p, m in self.scenarios())


def _pos_key(pos: str | None) -> str:
    p = (pos or "M")[:1].upper()
    return p if p in POSITION_PRIOR_90 else "M"


def _rates90(player_id: int, pos: str) -> tuple[dict[str, float], float, int]:
    rows = db.player_last_n_stats(player_id, n=20)
    w = recency_weights(len(rows), PLAYER_HALF_LIFE)
    mins = sum(wi * (r["minutes_played"] or 0) for wi, r in zip(w, rows))
    prior = POSITION_PRIOR_90[pos]
    out = {}
    for stat in STATS:
        if stat == "cards":
            events = sum(wi * ((r["yellow_card"] or 0) + (r["red_card"] or 0)) for wi, r in zip(w, rows))
        else:
            events = sum(wi * (r[stat] or 0) for wi, r in zip(w, rows))
        out[stat] = (events + prior[stat] * PRIOR_MINUTES / 90.0) / ((mins + PRIOR_MINUTES) / 90.0)
    raw_mins = sum((r["minutes_played"] or 0) for r in rows)
    return out, raw_mins, len(rows)


def team_outlooks(team_id: int, team_history: list[dict[str, Any]], lineup: dict[int, bool]) -> list[PlayerOutlook]:
    recent = team_history[:TEAM_WINDOW]
    match_ids = [r["match_id"] for r in recent]
    apps = db.team_player_appearances(team_id, match_ids)
    if not apps:
        return []
    order = {mid: i for i, mid in enumerate(match_ids)}
    weights = recency_weights(len(match_ids), 4.0)
    total_w = sum(weights)

    by_player: dict[int, list] = {}
    for a in apps:
        by_player.setdefault(int(a["player_id"]), []).append(a)

    out: list[PlayerOutlook] = []
    for pid, rows in by_player.items():
        start_w = sub_w = 0.0
        start_mins, sub_mins = [], []
        for r in rows:
            wi = weights[order[r["match_id"]]]
            mins = r["minutes_played"] or 0
            if mins <= 0:
                continue
            if r["substitute"]:
                sub_w += wi
                sub_mins.append(mins)
            else:
                start_w += wi
                start_mins.append(mins)
        p_start, p_sub = start_w / total_w, sub_w / total_w
        lineup_status = None
        if lineup:
            if pid not in lineup:
                continue  # not in the matchday squad
            if lineup[pid]:
                p_start, p_sub, lineup_status = 1.0, 0.0, "starting"
            else:
                # On the bench: roughly how often benched players get on.
                p_start, p_sub, lineup_status = 0.0, 0.55, "bench"
        if p_start + p_sub < 0.25 and lineup_status is None:
            continue  # fringe player; props would be noise
        pos = _pos_key(rows[0]["position"] or rows[0]["player_position"])
        rates, mins_sample, n_apps = _rates90(pid, pos)
        out.append(
            PlayerOutlook(
                player_id=pid,
                name=rows[0]["player_name"],
                team_id=team_id,
                position=pos,
                p_start=p_start,
                p_sub=p_sub,
                mins_start=sum(start_mins) / len(start_mins) if start_mins else 82.0,
                mins_sub=sum(sub_mins) / len(sub_mins) if sub_mins else 22.0,
                rates90=rates,
                minutes_sample=mins_sample,
                apps_sample=n_apps,
                lineup_status=lineup_status,
            )
        )
    return out
