"""Team-level expected values for goals, corners, cards, shots.

Each team gets an "attack" (for) and "defence" (against) multiplier per metric,
relative to the league baseline for the venue it played at:

    attack = (sum_i w_i * for_i + K * mean_exp) / (sum_i w_i * exp_for_i + K * mean_exp)

i.e. a recency-weighted ratio of what they produced vs what an average team at
that venue produces, shrunk toward 1.0 by K pseudo-matches. Expected count for
the fixture is then

    lambda_home = league_home_avg * attack_home * defence_away
    lambda_away = league_away_avg * attack_away * defence_home
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import mean, pvariance
from typing import Any

from .. import db
from .dist import recency_weights

# metric -> (history "for" column, league-row home column, league-row away column, default home, default away)
METRICS: dict[str, tuple[str, str, str, float, float]] = {
    "goals":   ("goals",   "home_goals",   "away_goals",   1.50, 1.20),
    "corners": ("corners", "home_corners", "away_corners", 5.40, 4.40),
    "cards":   ("cards",   "home_cards",   "away_cards",   1.95, 2.15),
    "shots":   ("shots",   "home_shots",   "away_shots",  13.50, 11.00),
    "sot":     ("sot",     "home_sot",     "away_sot",     4.70, 3.80),
}
DEFAULT_VAR_FACTOR = {"goals": 1.0, "corners": 1.25, "cards": 1.3, "shots": 1.3, "sot": 1.2}

HISTORY_N = int(os.getenv("HISTORY_MATCHES", "20"))  # most recent finished matches per team (all competitions)
HALF_LIFE = 10.0      # matches; the 10th most recent game counts half as much as the latest
SHRINK_K = 5.0        # pseudo-matches of league average mixed in; tuned on simulated seasons (opponent-adjusted RMSE flattens ~5-6)
REF_SHRINK_K = 8.0    # pseudo-matches for the referee card factor
MIN_LEAGUE_ROWS = 30  # below this the league baseline falls back to global / defaults


@dataclass
class Baseline:
    home: dict[str, float]
    away: dict[str, float]
    var_factor: dict[str, float]
    sample: dict[str, int]
    source: str


@dataclass
class Strength:
    attack: float
    defence: float
    n: int  # matches with this metric available


@dataclass
class TeamProfile:
    team_id: int
    history: list[dict[str, Any]]
    strengths: dict[str, Strength] = field(default_factory=dict)

    @property
    def last_match_date(self) -> str | None:
        return self.history[0]["date"] if self.history else None


def league_baseline(league: int | None, before: str) -> Baseline:
    since = (datetime.fromisoformat(before[:10]) - timedelta(days=400)).date().isoformat()
    rows = db.league_match_rows(league, since, before)
    source = f"league {league}"
    if len(rows) < MIN_LEAGUE_ROWS:
        rows = db.league_match_rows(None, since, before)
        source = "all stored matches"
    home, away, var_factor, sample = {}, {}, {}, {}
    for metric, (_, hcol, acol, dh, da) in METRICS.items():
        pairs = [(r[hcol], r[acol]) for r in rows if r[hcol] is not None and r[acol] is not None]
        sample[metric] = len(pairs)
        if len(pairs) >= MIN_LEAGUE_ROWS:
            home[metric] = max(mean(p[0] for p in pairs), 0.05)
            away[metric] = max(mean(p[1] for p in pairs), 0.05)
            totals = [p[0] + p[1] for p in pairs]
            m = mean(totals)
            # Variance of the total includes genuine between-match differences in
            # expected count, which our model already explains in part, so damp it.
            raw = pvariance(totals) / m if m > 0 else 1.0
            var_factor[metric] = 1.0 if metric == "goals" else min(max(1.0 + 0.6 * (raw - 1.0), 1.0), 1.8)
        else:
            home[metric], away[metric] = dh, da
            var_factor[metric] = DEFAULT_VAR_FACTOR[metric]
    if all(sample[m] < MIN_LEAGUE_ROWS for m in METRICS):
        source = "defaults (not enough stored matches)"
    return Baseline(home, away, var_factor, sample, source)


def _fit(hist: list[dict[str, Any]], base: Baseline, opp: dict[int, dict[str, Strength]] | None) -> dict[str, Strength]:
    out: dict[str, Strength] = {}
    for metric, (col, *_rest) in METRICS.items():
        rows = [r for r in hist if r.get(f"{col}_for") is not None and r.get(f"{col}_against") is not None]
        w = recency_weights(len(rows), HALF_LIFE)
        num_f = den_f = num_a = den_a = 0.0
        for wi, r in zip(w, rows):
            o = (opp or {}).get(r["opponent_id"], {}).get(metric)
            # What an average team would have produced against *this* opponent at this venue.
            exp_for = (base.home[metric] if r["is_home"] else base.away[metric]) * (o.defence if o else 1.0)
            exp_against = (base.away[metric] if r["is_home"] else base.home[metric]) * (o.attack if o else 1.0)
            num_f += wi * r[f"{col}_for"]
            den_f += wi * exp_for
            num_a += wi * r[f"{col}_against"]
            den_a += wi * exp_against
        prior = (base.home[metric] + base.away[metric]) / 2 * SHRINK_K
        out[metric] = Strength(
            attack=(num_f + prior) / (den_f + prior),
            defence=(num_a + prior) / (den_a + prior),
            n=len(rows),
        )
    return out


def team_profile(team_id: int, before: str, base: Baseline, n: int = HISTORY_N) -> TeamProfile:
    """Strengths without opponent adjustment (one pass)."""
    hist = db.team_history(team_id, before, n)
    return TeamProfile(team_id, hist, _fit(hist, base, None))


def match_profiles(home_id: int, away_id: int, before: str, base: Baseline, n: int = HISTORY_N) -> tuple[TeamProfile, TeamProfile]:
    """Opponent-adjusted strengths for both sides of a fixture.

    Pass 1 fits every opponent the two teams have faced without adjustment;
    pass 2 refits the two teams, crediting goals/corners against strong
    defences more than the same output against weak ones. Without this,
    a bottom side's numbers look average simply because it has played a
    full league and the model under-rates mismatches.
    """
    home = TeamProfile(home_id, db.team_history(home_id, before, n))
    away = TeamProfile(away_id, db.team_history(away_id, before, n))
    opp_ids = {r["opponent_id"] for r in home.history + away.history}
    first: dict[int, dict[str, Strength]] = {}
    for t in opp_ids | {home_id, away_id}:
        hist = home.history if t == home_id else away.history if t == away_id else db.team_history(t, before, n)
        if hist:
            first[t] = _fit(hist, base, None)
    home.strengths = _fit(home.history, base, first)
    away.strengths = _fit(away.history, base, first)
    return home, away


def expected_counts(metric: str, home: TeamProfile, away: TeamProfile, base: Baseline) -> tuple[float, float]:
    h, a = home.strengths[metric], away.strengths[metric]
    return (
        base.home[metric] * h.attack * a.defence,
        base.away[metric] * a.attack * h.defence,
    )


def referee_factor(referee_id: int | None, before: str, base: Baseline) -> tuple[float, dict[str, Any]]:
    """Multiplier on expected cards for this referee, shrunk toward 1.0."""
    league_total = base.home["cards"] + base.away["cards"]
    if referee_id is None:
        return 1.0, {"referee_matches": 0, "note": "referee not yet appointed"}
    cards = db.referee_match_cards(referee_id, before)
    shrunk = (sum(cards) + REF_SHRINK_K * league_total) / (len(cards) + REF_SHRINK_K)
    return shrunk / league_total, {
        "referee_matches": len(cards),
        "referee_raw_avg_cards": round(mean(cards), 2) if cards else None,
        "referee_shrunk_avg_cards": round(shrunk, 2),
    }
