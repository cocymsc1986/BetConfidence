"""SQLite schema and query helpers for Pitch Edge."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

DB_PATH = Path(os.getenv("PITCH_EDGE_DB", Path(__file__).resolve().parent / "pitch_edge.db"))

# API-Football short statuses for a match that has a final result. Matches that
# went to extra time / penalties are still valid history (the 90-minute stats
# are what most markets settle on, but excluding them entirely drops cup games).
FINISHED_STATUSES = ("FT", "AET", "PEN")
_FINISHED_SQL = "('FT','AET','PEN')"

# Sentinel stored in odds_quotes.line for markets that have no line (1X2, BTTS,
# anytime scorer). SQLite primary keys treat NULLs as distinct, so we can't use NULL.
NO_LINE = -1.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    league       INTEGER,
    country      TEXT
);

CREATE TABLE IF NOT EXISTS referees (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,
    country      TEXT
);

CREATE TABLE IF NOT EXISTS players (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    team_id      INTEGER REFERENCES teams(id),
    position     TEXT,
    nationality  TEXT,
    age          INTEGER
);

CREATE TABLE IF NOT EXISTS matches (
    id           INTEGER PRIMARY KEY,
    home_team    INTEGER NOT NULL REFERENCES teams(id),
    away_team    INTEGER NOT NULL REFERENCES teams(id),
    date         TEXT NOT NULL,
    league       INTEGER NOT NULL,
    referee_id   INTEGER REFERENCES referees(id),
    home_corners INTEGER,
    away_corners INTEGER,
    home_goals   INTEGER,
    away_goals   INTEGER,
    status       TEXT
);

CREATE TABLE IF NOT EXISTS team_match_stats (
    match_id         INTEGER NOT NULL REFERENCES matches(id),
    team_id          INTEGER NOT NULL REFERENCES teams(id),
    corners          INTEGER,
    shots_total      INTEGER,
    shots_on_target  INTEGER,
    fouls            INTEGER,
    yellow_cards     INTEGER,
    red_cards        INTEGER,
    possession       REAL,
    PRIMARY KEY (match_id, team_id)
);

CREATE TABLE IF NOT EXISTS player_match_stats (
    player_id        INTEGER NOT NULL REFERENCES players(id),
    match_id         INTEGER NOT NULL REFERENCES matches(id),
    team_id          INTEGER,
    substitute       INTEGER DEFAULT 0,
    goals            INTEGER DEFAULT 0,
    assists          INTEGER DEFAULT 0,
    shots_total      INTEGER DEFAULT 0,
    shots_on_target  INTEGER DEFAULT 0,
    yellow_card      INTEGER DEFAULT 0,
    red_card         INTEGER DEFAULT 0,
    fouls_committed  INTEGER DEFAULT 0,
    fouls_drawn      INTEGER DEFAULT 0,
    minutes_played   INTEGER DEFAULT 0,
    position         TEXT,
    PRIMARY KEY (player_id, match_id)
);

CREATE TABLE IF NOT EXISTS lineups (
    match_id     INTEGER NOT NULL REFERENCES matches(id),
    team_id      INTEGER NOT NULL,
    player_id    INTEGER NOT NULL,
    starter      INTEGER NOT NULL,
    PRIMARY KEY (match_id, player_id)
);

-- One row per bookmaker price. Markets are normalised to canonical keys (see odds.py).
CREATE TABLE IF NOT EXISTS odds_quotes (
    match_id     INTEGER NOT NULL,
    source       TEXT NOT NULL,
    bookmaker    TEXT NOT NULL,
    market       TEXT NOT NULL,
    selection    TEXT NOT NULL,
    line         REAL NOT NULL DEFAULT -1,
    player       TEXT NOT NULL DEFAULT '',
    price        REAL NOT NULL,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (match_id, source, bookmaker, market, selection, line, player)
);

-- Calls made per provider per UTC day, so free-plan quotas can be respected.
CREATE TABLE IF NOT EXISTS api_usage (
    day          TEXT NOT NULL,
    provider     TEXT NOT NULL,
    calls        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, provider)
);

CREATE INDEX IF NOT EXISTS idx_matches_date    ON matches(date);
CREATE INDEX IF NOT EXISTS idx_matches_league  ON matches(league);
CREATE INDEX IF NOT EXISTS idx_matches_home    ON matches(home_team);
CREATE INDEX IF NOT EXISTS idx_matches_away    ON matches(away_team);
CREATE INDEX IF NOT EXISTS idx_pms_player      ON player_match_stats(player_id);
CREATE INDEX IF NOT EXISTS idx_pms_match       ON player_match_stats(match_id);
CREATE INDEX IF NOT EXISTS idx_tms_team        ON team_match_stats(team_id);
CREATE INDEX IF NOT EXISTS idx_odds_match      ON odds_quotes(match_id);
"""

# Columns added after the first release; applied to pre-existing databases.
_MIGRATIONS = {
    "player_match_stats": {
        "team_id": "INTEGER",
        "substitute": "INTEGER DEFAULT 0",
    },
}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        for table, cols in _MIGRATIONS.items():
            existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if not existing:
                continue  # fresh DB; SCHEMA creates it with every column
            for col, decl in cols.items():
                if col not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        conn.executescript(SCHEMA)


def upsert(conn: sqlite3.Connection, table: str, row: dict[str, Any], keys: Iterable[str]) -> None:
    keys = tuple(keys)
    cols = list(row.keys())
    placeholders = ",".join("?" for _ in cols)
    col_list = ",".join(cols)
    update_clause = ",".join(f"{c}=excluded.{c}" for c in cols if c not in keys)
    key_clause = ",".join(keys)
    sql = (
        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
        f"ON CONFLICT({key_clause}) DO UPDATE SET {update_clause}"
        if update_clause
        else f"INSERT OR IGNORE INTO {table} ({col_list}) VALUES ({placeholders})"
    )
    conn.execute(sql, [row[c] for c in cols])


# ---------- match / team queries ----------

_MATCH_SELECT = """
    SELECT m.*, ht.name AS home_name, at.name AS away_name, r.name AS referee_name
    FROM matches m
    JOIN teams ht ON ht.id = m.home_team
    JOIN teams at ON at.id = m.away_team
    LEFT JOIN referees r ON r.id = m.referee_id
"""


def get_match(match_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(_MATCH_SELECT + " WHERE m.id = ?", (match_id,)).fetchone()


def upcoming_matches(days: int = 7) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return list(
            conn.execute(
                _MATCH_SELECT
                + f"""
                WHERE date(m.date) BETWEEN date('now') AND date('now', ?)
                  AND (m.status IS NULL OR m.status NOT IN {_FINISHED_SQL})
                ORDER BY m.date ASC
                """,
                (f"+{int(days)} day",),
            )
        )


def matches_on(date_str: str) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return list(
            conn.execute(
                _MATCH_SELECT + " WHERE date(m.date) = date(?) ORDER BY m.date ASC",
                (date_str,),
            )
        )


def team_last_n_matches(
    team_id: int, n: int = 10, home_only: bool = False, away_only: bool = False
) -> list[sqlite3.Row]:
    if home_only:
        where, params = "home_team = ?", (team_id, n)
    elif away_only:
        where, params = "away_team = ?", (team_id, n)
    else:
        where, params = "(home_team = ? OR away_team = ?)", (team_id, team_id, n)
    with get_conn() as conn:
        return list(
            conn.execute(
                f"""
                SELECT * FROM matches
                WHERE {where} AND status IN {_FINISHED_SQL}
                ORDER BY date DESC
                LIMIT ?
                """,
                params,
            )
        )


def team_history(team_id: int, before: str, n: int = 15) -> list[dict[str, Any]]:
    """Finished matches for a team before `before`, newest first, from the team's perspective.

    Stat columns are None when /fixtures/statistics hasn't been pulled for that match.
    """
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT m.id AS match_id, m.date, m.league, m.referee_id,
                   (m.home_team = :t) AS is_home,
                   CASE WHEN m.home_team = :t THEN m.away_team ELSE m.home_team END AS opponent_id,
                   CASE WHEN m.home_team = :t THEN m.home_goals ELSE m.away_goals END AS goals_for,
                   CASE WHEN m.home_team = :t THEN m.away_goals ELSE m.home_goals END AS goals_against,
                   tf.corners AS corners_for,         ta.corners AS corners_against,
                   tf.shots_total AS shots_for,       ta.shots_total AS shots_against,
                   tf.shots_on_target AS sot_for,     ta.shots_on_target AS sot_against,
                   tf.fouls AS fouls_for,             ta.fouls AS fouls_against,
                   tf.yellow_cards AS yellows_for,    ta.yellow_cards AS yellows_against,
                   tf.red_cards AS reds_for,          ta.red_cards AS reds_against
            FROM matches m
            LEFT JOIN team_match_stats tf ON tf.match_id = m.id AND tf.team_id = :t
            LEFT JOIN team_match_stats ta ON ta.match_id = m.id
                 AND ta.team_id = CASE WHEN m.home_team = :t THEN m.away_team ELSE m.home_team END
            WHERE (m.home_team = :t OR m.away_team = :t)
              AND m.status IN {_FINISHED_SQL}
              AND m.home_goals IS NOT NULL
              AND m.date < :before
            ORDER BY m.date DESC
            LIMIT :n
            """,
            {"t": team_id, "before": before, "n": n},
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["cards_for"] = _cards(d["yellows_for"], d["reds_for"])
        d["cards_against"] = _cards(d["yellows_against"], d["reds_against"])
        out.append(d)
    return out


def _cards(yellow: int | None, red: int | None) -> int | None:
    if yellow is None and red is None:
        return None
    return (yellow or 0) + (red or 0)


def league_match_rows(league: int | None, since: str, before: str) -> list[dict[str, Any]]:
    """Finished matches (optionally in one league) with home/away team stats side by side."""
    league_clause = "AND m.league = :league" if league is not None else ""
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT m.id, m.home_goals, m.away_goals, m.referee_id,
                   h.corners AS home_corners, a.corners AS away_corners,
                   h.shots_total AS home_shots, a.shots_total AS away_shots,
                   h.shots_on_target AS home_sot, a.shots_on_target AS away_sot,
                   h.yellow_cards AS home_yellows, a.yellow_cards AS away_yellows,
                   h.red_cards AS home_reds, a.red_cards AS away_reds
            FROM matches m
            LEFT JOIN team_match_stats h ON h.match_id = m.id AND h.team_id = m.home_team
            LEFT JOIN team_match_stats a ON a.match_id = m.id AND a.team_id = m.away_team
            WHERE m.status IN {_FINISHED_SQL} AND m.home_goals IS NOT NULL
              AND m.date >= :since AND m.date < :before {league_clause}
            """,
            {"league": league, "since": since, "before": before},
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["home_cards"] = _cards(d["home_yellows"], d["home_reds"])
        d["away_cards"] = _cards(d["away_yellows"], d["away_reds"])
        out.append(d)
    return out


def referee_match_cards(referee_id: int, before: str, n: int = 30) -> list[int]:
    """Total cards (yellow + red, both teams) in this referee's recent matches with team stats."""
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT SUM(COALESCE(t.yellow_cards,0) + COALESCE(t.red_cards,0)) AS cards,
                   COUNT(t.team_id) AS sides
            FROM matches m
            JOIN team_match_stats t ON t.match_id = m.id
            WHERE m.referee_id = ? AND m.status IN {_FINISHED_SQL} AND m.date < ?
              AND t.yellow_cards IS NOT NULL
            GROUP BY m.id
            HAVING sides = 2
            ORDER BY m.date DESC
            LIMIT ?
            """,
            (referee_id, before, n),
        ).fetchall()
    return [int(r["cards"]) for r in rows]


def referee_card_rate(referee_id: int | None) -> float:
    """Average cards per match for this referee (0.0 if unknown)."""
    if referee_id is None:
        return 0.0
    cards = referee_match_cards(referee_id, before="9999-12-31")
    return sum(cards) / len(cards) if cards else 0.0


# ---------- player queries ----------

def team_player_appearances(team_id: int, match_ids: list[int]) -> list[sqlite3.Row]:
    """Every player stat line for `team_id` in the given matches (used for the player model)."""
    if not match_ids:
        return []
    marks = ",".join("?" for _ in match_ids)
    with get_conn() as conn:
        return list(
            conn.execute(
                f"""
                SELECT s.*, p.name AS player_name, p.position AS player_position, m.date
                FROM player_match_stats s
                JOIN players p ON p.id = s.player_id
                JOIN matches m ON m.id = s.match_id
                WHERE s.match_id IN ({marks})
                  AND (s.team_id = ? OR (s.team_id IS NULL AND p.team_id = ?))
                """,
                (*match_ids, team_id, team_id),
            )
        )


def player_last_n_stats(player_id: int, n: int = 10) -> list[sqlite3.Row]:
    """Last n matches where the player actually got on the pitch."""
    with get_conn() as conn:
        return list(
            conn.execute(
                f"""
                SELECT s.*, m.date, m.home_team, m.away_team, m.referee_id
                FROM player_match_stats s
                JOIN matches m ON m.id = s.match_id
                WHERE s.player_id = ? AND m.status IN {_FINISHED_SQL} AND s.minutes_played > 0
                ORDER BY m.date DESC
                LIMIT ?
                """,
                (player_id, n),
            )
        )


def player_full_stats(player_id: int) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return list(
            conn.execute(
                """
                SELECT s.*, m.date, m.home_team, m.away_team, m.league,
                       ht.name AS home_name, at.name AS away_name
                FROM player_match_stats s
                JOIN matches m ON m.id = s.match_id
                JOIN teams ht ON ht.id = m.home_team
                JOIN teams at ON at.id = m.away_team
                WHERE s.player_id = ?
                ORDER BY m.date DESC
                """,
                (player_id,),
            )
        )


def get_player(player_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()


def search_players(query: str, limit: int = 25) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return list(
            conn.execute(
                """
                SELECT p.*, t.name AS team_name
                FROM players p
                LEFT JOIN teams t ON t.id = p.team_id
                WHERE p.name LIKE ?
                ORDER BY p.name ASC
                LIMIT ?
                """,
                (f"%{query}%", limit),
            )
        )


def match_lineup(match_id: int) -> dict[int, bool]:
    """player_id -> starter flag, empty until lineups are published (~40 min before KO)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT player_id, starter FROM lineups WHERE match_id = ?", (match_id,)
        ).fetchall()
    return {int(r["player_id"]): bool(r["starter"]) for r in rows}


# ---------- odds ----------

def odds_for_match(match_id: int) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM odds_quotes WHERE match_id = ?", (match_id,)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["line"] = None if d["line"] == NO_LINE else d["line"]
        d["player"] = d["player"] or None
        out.append(d)
    return out


def best_price(match_id: int, market: str, selection: str, line: float | None = None) -> float | None:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT MAX(price) AS p FROM odds_quotes
            WHERE match_id = ? AND market = ? AND selection = ? AND line = ? AND player = ''
            """,
            (match_id, market, selection, NO_LINE if line is None else float(line)),
        ).fetchone()
    return float(row["p"]) if row and row["p"] is not None else None


# ---------- API usage ----------

def _utc_day() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).date().isoformat()


def api_calls_today(provider: str) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT calls FROM api_usage WHERE day = ? AND provider = ?", (_utc_day(), provider)
        ).fetchone()
    return int(row["calls"]) if row else 0


def record_api_call(provider: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO api_usage(day, provider, calls) VALUES (?, ?, 1) "
            "ON CONFLICT(day, provider) DO UPDATE SET calls = calls + 1",
            (_utc_day(), provider),
        )
