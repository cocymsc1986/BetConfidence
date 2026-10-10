"""Daily paper-trading run: rank the day's best bets across every target fixture.

The Odds API is the constrained source: it runs on its free plan (500
credits/month), so it gets one bulk 1X2 + goals call per league, costing 2
credits per region, and no per-event calls. Every other market's prices come
from API-Football's bulk odds for the day (1-2 calls per league).

API-Football on Pro (7,500 calls/day) also refreshes each fixture the way
Analyse does: both teams' recent matches in all competitions plus lineups.
Set API_FOOTBALL_DAILY_BUDGET to run on its free plan instead; the run then
skips the per-fixture refresh and backfills league stats within the cap.

Keep the SQLite file between runs (``PITCH_EDGE_DB``) so the history builds up.

    python -m backend.daily                     # today, top 10, prints a table
    python -m backend.daily --date 2026-10-11 --json picks.json
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from . import analysis, db, ingest, odds  # noqa: E402

DAYS_BACK = int(os.getenv("DAILY_DAYS_BACK", "365"))


def refresh(date_str: str) -> dict[str, Any]:
    report: dict[str, Any] = {}
    report["fixtures"] = ingest.refresh(days_back=DAYS_BACK, days_forward=2, fetch_stats=False)
    report["api_football_odds"] = odds.refresh_api_football_odds_for_date(date_str)
    report["the_odds_api"] = odds.refresh_match_odds() if odds.ODDS_KEY else {"ok": False, "error": "ODDS_API_KEY not set"}
    if not ingest.DAILY_BUDGET:
        report["per_fixture"] = {
            int(m["id"]): ingest.refresh_for_match(int(m["id"]))
            for m in db.matches_on(date_str)
            if m["status"] not in db.FINISHED_STATUSES
        }
    report["stats_backfill"] = ingest.enrich_backlog(keep=0)
    report["api_football_calls_left"] = ingest.calls_left()
    report["the_odds_api_credits_left"] = odds._toa_remaining
    return report


def pick(date_str: str, *, top: int = 10, min_prob: float = 0.5) -> list[dict[str, Any]]:
    """Best bets on `date_str` with model probability above `min_prob` and positive edge."""
    picks: list[dict[str, Any]] = []
    for m in db.matches_on(date_str):
        if m["status"] in db.FINISHED_STATUSES:
            continue
        result = analysis.analyse_match(int(m["id"]), refresh=False, top=50)
        if not result:
            continue
        for b in result["top_bets"]:
            if b["probability"] <= min_prob or (b.get("ev") or 0) <= 0:
                continue
            picks.append({
                "match_id": m["id"],
                "kickoff": m["date"],
                "match": f"{m['home_name']} v {m['away_name']}",
                "bet": b["label"],
                "market": b["market"],
                "selection": b["selection"],
                "line": b["line"],
                "player": b["player"],
                "probability": b["probability"],
                "market_prob": b.get("market_prob"),
                "odds": b["best_odds"],
                "bookmaker": b["bookmaker"],
                "edge": b["ev"],
                "value_score": b["value_score"],
                "stake_pct": b.get("quarter_kelly_stake_pct"),
            })
    picks.sort(key=lambda p: -p["value_score"])
    return picks[:top]


def run(date_str: str | None = None, *, top: int = 10, min_prob: float = 0.5, refresh_data: bool = True) -> dict[str, Any]:
    date_str = date_str or date.today().isoformat()
    db.init_db()
    report = refresh(date_str) if refresh_data else None
    return {
        "date": date_str,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fixtures": len(db.matches_on(date_str)),
        "picks": pick(date_str, top=top, min_prob=min_prob),
        "refresh": report,
    }


def _table(out: dict[str, Any]) -> str:
    lines = [
        f"Paper picks for {out['date']} ({out['fixtures']} fixtures)",
        "",
        "| # | Match | Bet | Model % | Odds | Bookmaker | Edge |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for i, p in enumerate(out["picks"], 1):
        lines.append(
            f"| {i} | {p['match']} | {p['bet']} | {100 * p['probability']:.0f}% | {p['odds']:.2f} "
            f"| {p['bookmaker']} | {100 * p['edge']:+.1f}% |"
        )
    if not out["picks"]:
        lines.append("| - | No bets above the thresholds today | | | | | |")
    r = out.get("refresh")
    if r:
        lines += [
            "",
            f"API-Football calls left today: {r['api_football_calls_left']}; "
            f"stats backfilled: {r['stats_backfill'].get('enriched', 0)}, "
            f"still to backfill: {r['stats_backfill'].get('backlog', '?')}; "
            f"The Odds API credits left: {r['the_odds_api_credits_left']}",
        ]
        per_fixture = r.get("per_fixture") or {}
        sections = {k: r.get(k) or {} for k in ("fixtures", "api_football_odds", "the_odds_api", "stats_backfill")}
        sections["per_fixture"] = {"errors": [e for res in per_fixture.values() for e in res.get("errors") or []]}
        for name, res in sections.items():
            errs = res.get("errors") or ([res["error"]] if res.get("error") else [])
            if errs:
                lines.append(f"{name}: {errs[0][:200]}" + (f" (+{len(errs) - 1} more)" if len(errs) > 1 else ""))
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--date", help="YYYY-MM-DD (default: today)")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--min-prob", type=float, default=0.5)
    ap.add_argument("--no-refresh", action="store_true", help="use cached data only (no API calls)")
    ap.add_argument("--json", help="also write the full result to this file")
    a = ap.parse_args()
    out = run(a.date, top=a.top, min_prob=a.min_prob, refresh_data=not a.no_refresh)
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(out, indent=2, default=str))
    print(_table(out))


if __name__ == "__main__":
    main()
