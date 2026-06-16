"""SQLite schema and query helpers for Pitch Edge."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

DB_PATH = Path(os.getenv("PITCH_EDGE_DB", Path(__file__).resolve().parent / "pitch_edge.db"))

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

CREATE TABLE IF NOT EXISTS player_match_stats (
    player_id        INTEGER NOT NULL REFERENCES players(id),
    match_id         INTEGER NOT NULL REFERENCES matches(id),
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

CREATE TABLE IF NOT EXISTS odds_cache (
    match_id     INTEGER NOT NULL,
    market       TEXT NOT NULL,
    selection    TEXT NOT NULL,
    odds         REAL NOT NULL,
    bookmaker    TEXT,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (match_id, market, selection)
);

CREATE INDEX IF NOT EXISTS idx_matches_date    ON matches(date);
CREATE INDEX IF NOT EXISTS idx_matches_league  ON matches(league);
CREATE INDEX IF NOT EXISTS idx_pms_player      ON player_match_stats(player_id);
CREATE INDEX IF NOT EXISTS idx_pms_match       ON player_match_stats(match_id);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
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
        conn.executescript(SCHEMA)


def upsert(conn: sqlite3.Connection, table: str, row: dict[str, Any], keys: Iterable[str]) -> None:
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


# ---------- query helpers used by the signal engine ----------

def player_last_n_stats(player_id: int, n: int = 10) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return list(
            conn.execute(
                """
                SELECT s.*, m.date, m.home_team, m.away_team, m.referee_id
                FROM player_match_stats s
                JOIN matches m ON m.id = s.match_id
                WHERE s.player_id = ? AND m.status = 'FT'
                ORDER BY m.date DESC
                LIMIT ?
                """,
                (player_id, n),
            )
        )


def team_last_n_matches(team_id: int, n: int = 10, home_only: bool = False, away_only: bool = False) -> list[sqlite3.Row]:
    clause = ""
    if home_only:
        clause = "AND home_team = ?"
        params: tuple = (team_id, team_id, n)
    elif away_only:
        clause = "AND away_team = ?"
        params = (team_id, team_id, n)
    else:
        params = (team_id, team_id, n)
    with get_conn() as conn:
        return list(
            conn.execute(
                f"""
                SELECT * FROM matches
                WHERE (home_team = ? OR away_team = ?) {clause}
                  AND status = 'FT'
                ORDER BY date DESC
                LIMIT ?
                """,
                params,
            )
        )


def referee_card_rate(referee_id: int) -> float:
    """Average yellow cards per match dished out by this referee over stored matches."""
    if referee_id is None:
        return 0.0
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT AVG(card_count) AS rate FROM (
                SELECT m.id, COALESCE(SUM(s.yellow_card),0) AS card_count
                FROM matches m
                LEFT JOIN player_match_stats s ON s.match_id = m.id
                WHERE m.referee_id = ? AND m.status = 'FT'
                GROUP BY m.id
            )
            """,
            (referee_id,),
        ).fetchone()
    return float(row["rate"] or 0.0)


def upcoming_matches(days: int = 7) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return list(
            conn.execute(
                """
                SELECT m.*, ht.name AS home_name, at.name AS away_name, r.name AS referee_name
                FROM matches m
                JOIN teams ht ON ht.id = m.home_team
                JOIN teams at ON at.id = m.away_team
                LEFT JOIN referees r ON r.id = m.referee_id
                WHERE date(m.date) BETWEEN date('now') AND date('now', ? )
                ORDER BY m.date ASC
                """,
                (f'+{days} day',),
            )
        )


def matches_on(date_str: str) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return list(
            conn.execute(
                """
                SELECT m.*, ht.name AS home_name, at.name AS away_name, r.name AS referee_name
                FROM matches m
                JOIN teams ht ON ht.id = m.home_team
                JOIN teams at ON at.id = m.away_team
                LEFT JOIN referees r ON r.id = m.referee_id
                WHERE date(m.date) = date(?)
                ORDER BY m.date ASC
                """,
                (date_str,),
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


def players_for_match(match_id: int) -> list[sqlite3.Row]:
    """Players who have played for either side in this fixture (used to drive per-player signals)."""
    with get_conn() as conn:
        return list(
            conn.execute(
                """
                SELECT DISTINCT p.*, t.name AS team_name
                FROM players p
                JOIN teams t ON t.id = p.team_id
                JOIN matches m ON (m.home_team = p.team_id OR m.away_team = p.team_id)
                WHERE m.id = ?
                """,
                (match_id,),
            )
        )


def get_odds(match_id: int, market: str, selection: str | None = None) -> float | None:
    with get_conn() as conn:
        if selection is None:
            row = conn.execute(
                "SELECT odds FROM odds_cache WHERE match_id = ? AND market = ? LIMIT 1",
                (match_id, market),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT odds FROM odds_cache WHERE match_id = ? AND market = ? AND selection = ?",
                (match_id, market, selection),
            ).fetchone()
    return float(row["odds"]) if row else None
