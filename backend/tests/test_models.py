from backend.tests.conftest import player_name
from backend import db
from backend.model import markets, team
from backend.model.dist import count_pmf, prob_over


def _by(preds, market, selection, line=None, player=None):
    for p in preds:
        if p["market"] == market and p["selection"] == selection and p["line"] == line and p["player"] == player:
            return p
    return None


def test_team_last_n_matches_home_only_regression(league_db):
    # Used to raise "Incorrect number of bindings" (4 placeholders, 3 params),
    # which crashed every /signals request.
    home = db.team_last_n_matches(1, 5, home_only=True)
    away = db.team_last_n_matches(1, 5, away_only=True)
    assert len(home) == 5 and all(r["home_team"] == 1 for r in home)
    assert len(away) == 5 and all(r["away_team"] == 1 for r in away)


def test_negative_binomial_has_fatter_tails_and_same_mean():
    mean_nb = sum(k * count_pmf(k, 10, 1.3) for k in range(80))
    assert abs(mean_nb - 10) < 1e-6
    assert abs(sum(count_pmf(k, 10, 1.3) for k in range(80)) - 1) < 1e-9
    assert prob_over(14.5, 10, 1.3) > prob_over(14.5, 10, 1.0)


def test_goal_model_ranks_strong_team(league_db):
    m = db.get_match(league_db)
    res = markets.predict_match(m)
    preds = res["predictions"]
    home, draw, away = (_by(preds, "match_result", s)["probability"] for s in ("home", "draw", "away"))
    assert abs(home + draw + away - 1) < 0.01
    assert home > 0.6 > away  # team 1 (best) at home to team 10 (worst)
    for line in markets.GOAL_LINES:
        o, u = _by(preds, "total_goals", "over", line), _by(preds, "total_goals", "under", line)
        if o and u:
            assert abs(o["probability"] + u["probability"] - 1) < 0.011


def test_strengths_recover_truth(league_db):
    m = db.get_match(league_db)
    base = team.league_baseline(39, m["date"])
    assert base.source == "league 39"
    strong = team.team_profile(1, m["date"], base)
    weak = team.team_profile(10, m["date"], base)
    assert strong.strengths["goals"].attack > 1.2 > weak.strengths["goals"].attack
    assert strong.strengths["goals"].defence < weak.strengths["goals"].defence
    assert strong.strengths["corners"].attack > weak.strengths["corners"].attack
    # Simulated corners are over-dispersed only by team mix; factor stays in sane bounds.
    assert 1.0 <= base.var_factor["corners"] <= 1.8


def test_referee_factor_detects_strict_referee(league_db):
    m = db.get_match(league_db)
    base = team.league_baseline(39, m["date"])
    strict, _ = team.referee_factor(1, m["date"], base)
    lenient, _ = team.referee_factor(2, m["date"], base)
    unknown, _ = team.referee_factor(None, m["date"], base)
    assert strict > 1.15 and lenient < 0.85 and unknown == 1.0


def test_player_props(league_db):
    m = db.get_match(league_db)
    preds = markets.predict_match(m)["predictions"]
    striker = "Erling Haaland"
    keeper = player_name(100)
    shots = _by(preds, "player_shots", "over", 1.5, striker)
    assert shots and shots["probability"] > 0.6
    assert _by(preds, "player_goal", "yes", None, keeper) is None  # below MIN_P, not emitted
    # Unused subs (0 minutes) must not be modelled as likely starters.
    unused = [p for p in preds if p["player"] == player_name(113)]
    assert unused == []


def test_lineup_overrides_history(league_db):
    m = db.get_match(league_db)
    with db.get_conn() as conn:
        conn.execute("INSERT INTO lineups VALUES (1, 1, 109, 0)")  # striker benched
        conn.execute("INSERT INTO lineups VALUES (1, 1, 111, 1)")  # usual sub starts
    preds = markets.predict_match(m)["predictions"]
    benched = _by(preds, "player_shots", "over", 0.5, "Erling Haaland")
    assert benched["detail"]["lineup"] == "bench"
    assert benched["detail"]["start_probability"] == 0
    # Players not in the matchday squad are dropped entirely.
    assert not [p for p in preds if p["player"] == player_name(105)]
