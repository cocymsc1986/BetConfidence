"""Turn team + player models into probabilities for concrete betting markets.

Canonical market keys (shared with odds.py so prices can be joined):
  match_result        home / draw / away
  total_goals         over / under  (line)
  home_total_goals    over / under  (line)
  away_total_goals    over / under  (line)
  btts                yes / no
  total_corners       over / under  (line)
  home_total_corners  over / under  (line)
  away_total_corners  over / under  (line)
  total_cards         over / under  (line)
  player_goal         yes            (anytime scorer)
  player_assist       yes
  player_card         yes            (to be booked)
  player_shots        over (line)
  player_shots_on_target over (line)
"""
from __future__ import annotations

from statistics import mean
from typing import Any

from .. import db
from . import player as player_model
from . import team as team_model
from .dist import clamp_prob, poisson_pmf, prob_over

DC_RHO = -0.06  # Dixon-Coles low-score correlation; typical fitted values are -0.03..-0.12
MAX_GOALS = 10

GOAL_LINES = (0.5, 1.5, 2.5, 3.5, 4.5)
TEAM_GOAL_LINES = (0.5, 1.5, 2.5)
CORNER_LINES = (6.5, 7.5, 8.5, 9.5, 10.5, 11.5, 12.5, 13.5)
TEAM_CORNER_LINES = (2.5, 3.5, 4.5, 5.5, 6.5, 7.5)
CARD_LINES = (1.5, 2.5, 3.5, 4.5, 5.5, 6.5)
PLAYER_SHOT_LINES = (0.5, 1.5, 2.5, 3.5)
PLAYER_SOT_LINES = (0.5, 1.5, 2.5)

# Don't emit near-certain / near-impossible selections: they can't be value and clutter the list.
MIN_P, MAX_P = 0.04, 0.96


def _pred(market, selection, line, prob, label, category, reliability, detail=None, player=None) -> dict[str, Any]:
    prob = clamp_prob(prob)
    return {
        "market": market,
        "selection": selection,
        "line": line,
        "player": player.name if player else None,
        "player_id": player.player_id if player else None,
        "team_id": player.team_id if player else None,
        "label": label,
        "category": category,
        "probability": round(prob, 4),
        "fair_odds": round(1 / prob, 2),
        "reliability": round(reliability, 2),
        "detail": detail or {},
    }


def score_matrix(lam_h: float, lam_a: float, rho: float = DC_RHO) -> list[list[float]]:
    m = [[poisson_pmf(i, lam_h) * poisson_pmf(j, lam_a) for j in range(MAX_GOALS + 1)] for i in range(MAX_GOALS + 1)]
    # Dixon-Coles adjustment: real football has more 0-0/1-1 and fewer 1-0/0-1 than independent Poisson.
    m[0][0] *= max(1 - lam_h * lam_a * rho, 0)
    m[0][1] *= max(1 + lam_h * rho, 0)
    m[1][0] *= max(1 + lam_a * rho, 0)
    m[1][1] *= max(1 - rho, 0)
    total = sum(map(sum, m))
    return [[v / total for v in row] for row in m]


def _ou(market, line, p_over, label_noun, category, rel, detail):
    out = []
    if MIN_P <= p_over <= MAX_P:
        out.append(_pred(market, "over", line, p_over, f"Over {line} {label_noun}", category, rel, detail))
        out.append(_pred(market, "under", line, 1 - p_over, f"Under {line} {label_noun}", category, rel, detail))
    return out


def predict_match(match: Any) -> dict[str, Any]:
    """All market probabilities for a fixture row from db.get_match()."""
    before = match["date"]
    base = team_model.league_baseline(match["league"], before)
    home, away = team_model.match_profiles(match["home_team"], match["away_team"], before, base)
    hn, an = match["home_name"], match["away_name"]
    base_rel = 0.8 if base.source.startswith("defaults") else 1.0

    def rel(metric: str) -> float:
        n = min(home.strengths[metric].n, away.strengths[metric].n)
        return min(n / 10.0, 1.0) * base_rel

    preds: list[dict[str, Any]] = []
    warnings: list[str] = []

    # ---- goals ----
    lam_h, lam_a = team_model.expected_counts("goals", home, away, base)
    goals_detail = {
        "expected_goals_home": round(lam_h, 2),
        "expected_goals_away": round(lam_a, 2),
        "home_attack": round(home.strengths["goals"].attack, 2),
        "home_defence": round(home.strengths["goals"].defence, 2),
        "away_attack": round(away.strengths["goals"].attack, 2),
        "away_defence": round(away.strengths["goals"].defence, 2),
        "sample_home": home.strengths["goals"].n,
        "sample_away": away.strengths["goals"].n,
    }
    if min(home.strengths["goals"].n, away.strengths["goals"].n) < 3:
        warnings.append("Fewer than 3 finished matches for a team — run a refresh for this match first.")
    else:
        mat = score_matrix(lam_h, lam_a)
        r = rel("goals")
        p_home = sum(mat[i][j] for i in range(MAX_GOALS + 1) for j in range(MAX_GOALS + 1) if i > j)
        p_draw = sum(mat[i][i] for i in range(MAX_GOALS + 1))
        p_away = 1 - p_home - p_draw
        preds += [
            _pred("match_result", "home", None, p_home, f"{hn} to win", "result", r, goals_detail),
            _pred("match_result", "draw", None, p_draw, "Draw", "result", r, goals_detail),
            _pred("match_result", "away", None, p_away, f"{an} to win", "result", r, goals_detail),
        ]
        for line in GOAL_LINES:
            p = sum(mat[i][j] for i in range(MAX_GOALS + 1) for j in range(MAX_GOALS + 1) if i + j > line)
            preds += _ou("total_goals", line, p, "goals", "goals", r, goals_detail)
        for line in TEAM_GOAL_LINES:
            ph = sum(mat[i][j] for i in range(MAX_GOALS + 1) for j in range(MAX_GOALS + 1) if i > line)
            pa = sum(mat[i][j] for i in range(MAX_GOALS + 1) for j in range(MAX_GOALS + 1) if j > line)
            preds += _ou("home_total_goals", line, ph, f"{hn} goals", "goals", r, goals_detail)
            preds += _ou("away_total_goals", line, pa, f"{an} goals", "goals", r, goals_detail)
        p_btts = sum(mat[i][j] for i in range(1, MAX_GOALS + 1) for j in range(1, MAX_GOALS + 1))
        preds += [
            _pred("btts", "yes", None, p_btts, "Both teams to score", "goals", r, goals_detail),
            _pred("btts", "no", None, 1 - p_btts, "Both teams to score — No", "goals", r, goals_detail),
        ]

    # ---- corners ----
    if min(home.strengths["corners"].n, away.strengths["corners"].n) >= 3:
        ch, ca = team_model.expected_counts("corners", home, away, base)
        vf = base.var_factor["corners"]
        r = rel("corners")
        detail = {
            "expected_corners_home": round(ch, 2),
            "expected_corners_away": round(ca, 2),
            "expected_total": round(ch + ca, 2),
            "home_avg_won_last": _raw_avg(home.history, "corners_for"),
            "home_avg_conceded_last": _raw_avg(home.history, "corners_against"),
            "away_avg_won_last": _raw_avg(away.history, "corners_for"),
            "away_avg_conceded_last": _raw_avg(away.history, "corners_against"),
            "dispersion": round(vf, 2),
        }
        for line in CORNER_LINES:
            preds += _ou("total_corners", line, prob_over(line, ch + ca, vf), "corners", "corners", r, detail)
        for line in TEAM_CORNER_LINES:
            preds += _ou("home_total_corners", line, prob_over(line, ch, vf), f"{hn} corners", "corners", r, detail)
            preds += _ou("away_total_corners", line, prob_over(line, ca, vf), f"{an} corners", "corners", r, detail)
    else:
        warnings.append("Not enough corner data (needs /fixtures/statistics for recent matches).")

    # ---- cards ----
    ref_factor, ref_detail = team_model.referee_factor(match["referee_id"], before, base)
    if min(home.strengths["cards"].n, away.strengths["cards"].n) >= 3:
        kh, ka = team_model.expected_counts("cards", home, away, base)
        kh, ka = kh * ref_factor, ka * ref_factor
        vf = base.var_factor["cards"]
        detail = {
            "expected_cards_home": round(kh, 2),
            "expected_cards_away": round(ka, 2),
            "expected_total": round(kh + ka, 2),
            "referee": match["referee_name"],
            "referee_factor": round(ref_factor, 2),
            **ref_detail,
            "dispersion": round(vf, 2),
        }
        r = rel("cards") * (1.0 if match["referee_id"] else 0.85)
        for line in CARD_LINES:
            preds += _ou("total_cards", line, prob_over(line, kh + ka, vf), "cards", "cards", r, detail)
    else:
        warnings.append("Not enough card data for these teams.")

    # ---- players ----
    lineup = db.match_lineup(match["id"])
    player_preds, n_players = _player_predictions(match, home, away, base, ref_factor, lineup)
    preds += player_preds

    return {
        "predictions": preds,
        "warnings": warnings,
        "context": {
            "baseline": base.source,
            "baseline_sample": base.sample,
            "home_last_match": home.last_match_date,
            "away_last_match": away.last_match_date,
            "home_matches_used": len(home.history),
            "away_matches_used": len(away.history),
            "lineups_confirmed": bool(lineup),
            "players_modelled": n_players,
        },
    }


def _raw_avg(history: list[dict[str, Any]], col: str, n: int = 10) -> float | None:
    vals = [r[col] for r in history[:n] if r.get(col) is not None]
    return round(mean(vals), 2) if vals else None


def _player_predictions(match, home, away, base, ref_factor, lineup) -> tuple[list[dict[str, Any]], int]:
    out: list[dict[str, Any]] = []
    n = 0
    for prof, opp, is_home in ((home, away, True), (away, home, False)):
        outlooks = player_model.team_outlooks(prof.team_id, prof.history, lineup)
        n += len(outlooks)

        def venue(metric: str) -> float:
            avg = (base.home[metric] + base.away[metric]) / 2
            return (base.home[metric] if is_home else base.away[metric]) / avg

        mult = {
            "shots_total": opp.strengths["shots"].defence * venue("shots"),
            "shots_on_target": opp.strengths["sot"].defence * venue("sot"),
            "goals": opp.strengths["goals"].defence * venue("goals"),
            "assists": opp.strengths["goals"].defence * venue("goals"),
            "cards": ref_factor,
        }
        for pl in outlooks:
            rel = min(pl.minutes_sample / 900.0, 1.0) * (1.0 if pl.lineup_status == "starting" or pl.p_start >= 0.8 else 0.85)
            detail = {
                "position": pl.position,
                "lineup": pl.lineup_status or "not yet announced",
                "start_probability": round(pl.p_start, 2),
                "appearance_probability": round(pl.p_play, 2),
                "expected_minutes_if_plays": round(sum(p * m for p, m in pl.scenarios()), 0),
                "shots_per90": round(pl.rates90["shots_total"], 2),
                "sot_per90": round(pl.rates90["shots_on_target"], 2),
                "goals_per90": round(pl.rates90["goals"], 2),
                "assists_per90": round(pl.rates90["assists"], 2),
                "cards_per90": round(pl.rates90["cards"], 2),
                "opponent_shots_conceded_factor": round(mult["shots_total"], 2),
                "appearances_sampled": pl.apps_sample,
                "minutes_sampled": int(pl.minutes_sample),
                "note": "probabilities assume the player features (props are void otherwise)",
            }

            def add(market, selection, line, p, label):
                if MIN_P <= p <= MAX_P:
                    out.append(_pred(market, selection, line, p, label, "player", rel, detail, pl))

            add("player_goal", "yes", None, pl.prob_at_least("goals", 1, mult["goals"]), f"{pl.name} to score")
            add("player_assist", "yes", None, pl.prob_at_least("assists", 1, mult["assists"]), f"{pl.name} to assist")
            add("player_card", "yes", None, pl.prob_at_least("cards", 1, mult["cards"]), f"{pl.name} to be carded")
            for line in PLAYER_SHOT_LINES:
                k = int(line + 0.5)
                p = pl.prob_at_least("shots_total", k, mult["shots_total"], player_model.SHOTS_VAR_FACTOR)
                add("player_shots", "over", line, p, f"{pl.name} {k}+ shots")
            for line in PLAYER_SOT_LINES:
                k = int(line + 0.5)
                p = pl.prob_at_least("shots_on_target", k, mult["shots_on_target"], player_model.SHOTS_VAR_FACTOR)
                add("player_shots_on_target", "over", line, p, f"{pl.name} {k}+ shots on target")
    return out, n
