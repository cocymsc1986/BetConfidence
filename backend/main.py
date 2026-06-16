"""Pitch Edge FastAPI app."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from . import db, ingest, odds
from .signals import engine

app = FastAPI(title="Pitch Edge", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


@app.get("/")
def root() -> dict:
    return {"app": "Pitch Edge", "status": "ok"}


@app.get("/signals")
def get_signals(match_date: str = Query(default="today")) -> dict:
    date_str = date.today().isoformat() if match_date == "today" else match_date
    signals = engine.signals_for_date(date_str)
    return {"date": date_str, "count": len(signals), "signals": signals}


@app.get("/signals/best")
def get_best_signals(match_date: str = Query(default="today")) -> dict:
    date_str = date.today().isoformat() if match_date == "today" else match_date
    signals = engine.signals_for_date(date_str)
    best = [
        s for s in signals
        if (s.get("confidence") or 0) >= 0.65
        and (s.get("odds") or 0) >= 1.8
        and (s.get("ev_score") or 0) > 0.05
    ]
    return {"date": date_str, "count": len(best), "signals": best}


@app.get("/player/{player_id}/stats")
def get_player_stats(player_id: int) -> dict:
    rows = db.player_full_stats(player_id)
    if not rows:
        raise HTTPException(404, "player not found or has no recorded matches")
    return {
        "player_id": player_id,
        "matches": [dict(r) for r in rows],
        "signals": engine.signals_for_player(player_id),
    }


@app.get("/players/search")
def players_search(q: str = Query(min_length=2)) -> dict:
    rows = db.search_players(q)
    return {"results": [dict(r) for r in rows]}


@app.get("/matches/upcoming")
def get_upcoming(days: int = 7) -> dict:
    rows = db.upcoming_matches(days=days)
    out = []
    for r in rows:
        row = dict(r)
        # Surface bookmaker over/under and home win odds if cached.
        row["odds"] = {
            "home_win": db.get_odds(r["id"], "h2h", row["home_name"]),
            "draw": db.get_odds(r["id"], "h2h", "Draw"),
            "away_win": db.get_odds(r["id"], "h2h", row["away_name"]),
            "over_2_5": db.get_odds(r["id"], "totals", "Over|2.5"),
            "btts_yes": db.get_odds(r["id"], "btts", "Yes"),
        }
        out.append(row)
    return {"count": len(out), "matches": out}


@app.post("/ingest/refresh")
def ingest_refresh(days_back: int = 365, days_forward: int = 7, fetch_stats: bool = True) -> dict:
    result = ingest.refresh(days_back=days_back, days_forward=days_forward, fetch_stats=fetch_stats)
    odds_result = odds.refresh_match_odds()
    return {"fixtures": result, "odds": odds_result}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=True)
