"""Runs all market signal calculators, attaches odds + EV, returns a ranked list."""
from __future__ import annotations

from typing import Any

from .. import db
from . import btts, cards_over, corners, goalscorer, yellow_card


# Maps internal signal market name -> (odds_cache market key, optional selection prefix matcher)
ODDS_LOOKUP: dict[str, tuple[str, str | None]] = {
    "BTTS Yes": ("btts", "Yes"),
    "Over 2.5 Goals": ("totals", "Over|2.5"),
    "Over 9.5 Corners": ("alternate_totals_corners", "Over|9.5"),
    "Over 3.5 Cards": ("alternate_totals_cards", "Over|3.5"),
    "Anytime Goalscorer": ("player_goal_scorer_anytime", None),
    "Anytime Assist": ("player_assist_anytime", None),
    "Player Yellow Card": ("player_card_yellow", None),
}


def _attach_odds(signal: dict[str, Any]) -> dict[str, Any]:
    market_name = signal["market"]
    cache_key, selection = ODDS_LOOKUP.get(market_name, (None, None))
    odds = None
    if cache_key:
        odds = db.get_odds(signal["match_id"], cache_key, selection)
    signal["odds"] = odds
    if odds and signal.get("confidence") is not None:
        signal["ev_score"] = round(signal["confidence"] * odds - 1, 4)
    else:
        signal["ev_score"] = None
    return signal


def _match_level_signals(match_row) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for fn in (
        lambda: btts.btts(match_row["id"], match_row["home_team"], match_row["away_team"]),
        lambda: btts.over_2_5(match_row["id"], match_row["home_team"], match_row["away_team"]),
        lambda: corners.over_9_5(match_row["id"], match_row["home_team"], match_row["away_team"]),
        lambda: cards_over.over_3_5_cards(
            match_row["id"], match_row["home_team"], match_row["away_team"], match_row["referee_id"]
        ),
    ):
        sig = fn()
        if sig:
            out.append(sig)
    return out


def _player_signals(match_row, players) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in players:
        for sig in (
            yellow_card.calculate(p["id"], match_row["id"], match_row["referee_id"]),
            goalscorer.goalscorer(p["id"], match_row["id"]),
            goalscorer.assist(p["id"], match_row["id"]),
        ):
            if not sig:
                continue
            sig["player_name"] = p["name"]
            sig["team_name"] = p["team_name"]
            out.append(sig)
    return out


def signals_for_matches(matches) -> list[dict[str, Any]]:
    all_signals: list[dict[str, Any]] = []
    for m in matches:
        ctx = {
            "match_id": m["id"],
            "home_team": m["home_team"],
            "away_team": m["away_team"],
            "home_name": m["home_name"],
            "away_name": m["away_name"],
            "date": m["date"],
            "referee": m["referee_name"],
        }
        match_sigs = _match_level_signals(m)
        players = db.players_for_match(m["id"])
        player_sigs = _player_signals(m, players)
        for sig in match_sigs + player_sigs:
            sig["match_context"] = ctx
            all_signals.append(_attach_odds(sig))

    # Rank: signals with EV first (descending), then by raw confidence.
    def sort_key(s: dict[str, Any]) -> tuple[int, float, float]:
        has_ev = 1 if s.get("ev_score") is not None else 0
        return (-has_ev, -(s.get("ev_score") or 0.0), -(s.get("confidence") or 0.0))

    all_signals.sort(key=sort_key)
    return all_signals


def signals_for_date(date_str: str) -> list[dict[str, Any]]:
    return signals_for_matches(db.matches_on(date_str))


def signals_for_player(player_id: int) -> list[dict[str, Any]]:
    with db.get_conn() as conn:
        upcoming = list(
            conn.execute(
                """
                SELECT m.*, ht.name AS home_name, at.name AS away_name, r.name AS referee_name
                FROM matches m
                JOIN teams ht ON ht.id = m.home_team
                JOIN teams at ON at.id = m.away_team
                LEFT JOIN referees r ON r.id = m.referee_id
                JOIN players p ON p.id = ?
                WHERE (m.home_team = p.team_id OR m.away_team = p.team_id)
                  AND date(m.date) >= date('now')
                ORDER BY m.date ASC
                LIMIT 5
                """,
                (player_id,),
            )
        )
    all_signals: list[dict[str, Any]] = []
    for m in upcoming:
        ctx = {
            "match_id": m["id"],
            "home_name": m["home_name"],
            "away_name": m["away_name"],
            "date": m["date"],
            "referee": m["referee_name"],
        }
        for sig in (
            yellow_card.calculate(player_id, m["id"], m["referee_id"]),
            goalscorer.goalscorer(player_id, m["id"]),
            goalscorer.assist(player_id, m["id"]),
        ):
            if sig:
                sig["match_context"] = ctx
                all_signals.append(_attach_odds(sig))
    all_signals.sort(key=lambda s: -(s.get("ev_score") or 0.0))
    return all_signals
