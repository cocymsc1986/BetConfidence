"""Shared fixtures: a temp SQLite DB filled with a simulated league with known team strengths."""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

import pytest

from backend import db

N_TEAMS = 10
# Team 1 is much stronger than team 10; the model should notice.
ATTACK = {t: 1.5 - 0.1 * (t - 1) for t in range(1, N_TEAMS + 1)}
DEFENCE = {t: 0.6 + 0.08 * (t - 1) for t in range(1, N_TEAMS + 1)}
CORNER_ATT = {t: 1.3 - 0.06 * (t - 1) for t in range(1, N_TEAMS + 1)}


def player_name(pid: int) -> str:
    """Distinct, digit-free names ("Bjorn Abaj"), like real ones; team 1's striker is Erling Haaland."""
    if pid == 109:
        return "Erling Haaland"
    letters = "".join(chr(97 + int(d)) for d in str(pid))
    return f"{letters[::-1].capitalize()} {letters.capitalize()}son"


def _poisson(rng: random.Random, lam: float) -> int:
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= L:
            return k
        k += 1


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    return tmp_path / "test.db"


@pytest.fixture()
def league_db(tmp_db):
    return build_league()


def build_league() -> int:
    """Two round-robins (180 matches) over the last ~300 days, plus one upcoming fixture 1 v 10."""
    rng = random.Random(42)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    start = now - timedelta(days=300)
    with db.get_conn() as conn:
        for t in range(1, N_TEAMS + 1):
            conn.execute("INSERT INTO teams(id, name, league) VALUES (?,?,39)", (t, f"Team {t}"))
            for k in range(14):
                pid = t * 100 + k
                pos = "G" if k == 0 else "D" if k < 5 else "M" if k < 9 else "F"
                conn.execute("INSERT INTO players(id, name, team_id, position) VALUES (?,?,?,?)",
                             (pid, player_name(pid), t, pos))
        conn.execute("INSERT INTO referees(id, name) VALUES (1, 'Strict Ref'), (2, 'Lenient Ref')")

        fixtures = [(h, a) for h in range(1, N_TEAMS + 1) for a in range(1, N_TEAMS + 1) if h != a]
        fixtures = fixtures * 2
        rng.shuffle(fixtures)
        mid = 1000
        for i, (h, a) in enumerate(fixtures):
            when = start + timedelta(hours=int(i * 300 * 24 / len(fixtures)))
            lam_h = 1.45 * ATTACK[h] * DEFENCE[a]
            lam_a = 1.15 * ATTACK[a] * DEFENCE[h]
            hg, ag = _poisson(rng, lam_h), _poisson(rng, lam_a)
            ref = 1 if i % 2 == 0 else 2
            ref_mult = 1.5 if ref == 1 else 0.7
            hc, ac = _poisson(rng, 5.4 * CORNER_ATT[h]), _poisson(rng, 4.4 * CORNER_ATT[a])
            hy, ay = _poisson(rng, 1.9 * ref_mult), _poisson(rng, 2.1 * ref_mult)
            conn.execute(
                "INSERT INTO matches(id, home_team, away_team, date, league, referee_id, home_goals, away_goals,"
                " home_corners, away_corners, status) VALUES (?,?,?,?,39,?,?,?,?,?,'FT')",
                (mid, h, a, when.isoformat(), ref, hg, ag, hc, ac),
            )
            for team, c, y, shots in ((h, hc, hy, _poisson(rng, 13)), (a, ac, ay, _poisson(rng, 11))):
                conn.execute(
                    "INSERT INTO team_match_stats VALUES (?,?,?,?,?,?,?,?,?)",
                    (mid, team, c, shots, max(shots // 3, 0), 11, y, 0, 50.0),
                )
            # Player lines: 11 starters (k=0..10) + 3 subs; striker k=9 is the shooter.
            for team, goals in ((h, hg), (a, ag)):
                scorers = [team * 100 + rng.choice([5, 6, 7, 8, 9, 9, 9, 10, 10]) for _ in range(goals)]
                for k in range(14):
                    pid = team * 100 + k
                    starter = k < 11
                    mins = 90 if starter else (25 if k < 13 else 0)
                    shots = _poisson(rng, (3.5 if k == 9 else 1.0 if k >= 5 else 0.3) * mins / 90)
                    conn.execute(
                        "INSERT INTO player_match_stats(player_id, match_id, team_id, substitute, goals, assists,"
                        " shots_total, shots_on_target, yellow_card, red_card, fouls_committed, fouls_drawn,"
                        " minutes_played, position) VALUES (?,?,?,?,?,0,?,?,?,0,1,1,?,?)",
                        (pid, mid, team, 0 if starter else 1, scorers.count(pid), max(shots, scorers.count(pid)),
                         min(shots, max(scorers.count(pid), shots // 2)), 1 if (k == 3 and rng.random() < 0.4) else 0,
                         mins, "G" if k == 0 else "D" if k < 5 else "M" if k < 9 else "F"),
                    )
            mid += 1

        conn.execute(
            "INSERT INTO matches(id, home_team, away_team, date, league, referee_id, status)"
            " VALUES (1, 1, 10, ?, 39, 1, 'NS')",
            ((now + timedelta(days=1)).isoformat(),),
        )
    return 1  # upcoming match id
