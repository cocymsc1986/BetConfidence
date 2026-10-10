"""Combine model probabilities with bookmaker prices and rank value bets.

For each selection we compute:
  best_odds        best price among allowed bookmakers (ODDS_BOOKMAKERS)
  market_prob      de-margined consensus probability across *all* bookmakers
                   (only for markets where every outcome is priced: 1X2, O/U, BTTS)
  model_prob       our model's probability
  blended_prob     reliability-weighted mix of model and market. Bookmaker
                   consensus is a strong signal; a model that disagrees wildly
                   with every bookmaker is far more often wrong than right, so
                   ranking on the blend stops data glitches from topping the list.
  ev               model_prob * best_odds - 1           (the headline "edge")
  value_score      blended_prob * best_odds - 1         (what we rank on)
"""
from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any

from .odds import bookmaker_allowed, player_name_match

MIN_ODDS, MAX_ODDS = 1.15, 21.0
MAX_PER_PLAYER = 2
# When a market has no two-sided price (most player props), we can't strip the
# margin, so discount the score instead.
ONE_SIDED_PENALTY = 0.75

_COMPLEMENTS = {
    "match_result": ("home", "draw", "away"),
    "btts": ("yes", "no"),
}


def _key(market: str, selection: str, line: float | None, player: str | None) -> tuple:
    return (market, selection, None if line is None else round(float(line), 2), player)


def _resolve_players(predictions: list[dict[str, Any]], quotes: list[dict[str, Any]]) -> dict[str, str]:
    """Map bookmaker player names to our player names (bookmakers spell names their own way)."""
    ours = {p["player"] for p in predictions if p["player"]}
    mapping: dict[str, str] = {}
    for q in quotes:
        name = q.get("player")
        if not name or name in mapping:
            continue
        hits = [o for o in ours if player_name_match(name, o)]
        if len(hits) == 1:
            mapping[name] = hits[0]
    return mapping


def _market_probs(quotes: list[dict[str, Any]]) -> dict[tuple, tuple[float, int]]:
    """De-margined consensus probability per selection: median over bookmakers of p_i / sum(p)."""
    groups: dict[tuple, dict[str, list[tuple[str, float]]]] = defaultdict(lambda: defaultdict(list))
    for q in quotes:
        if q.get("player"):
            # Player over/under can be two-sided too
            if q["selection"] not in ("over", "under"):
                continue
        gkey = (q["market"], q["line"], q.get("player"))
        groups[gkey][q["bookmaker"]].append((q["selection"], q["price"]))

    per_sel: dict[tuple, list[float]] = defaultdict(list)
    for (market, line, player), books in groups.items():
        needed = _COMPLEMENTS.get(market, ("over", "under"))
        for prices in books.values():
            by_sel = {s: p for s, p in prices}
            if not all(s in by_sel for s in needed):
                continue
            inv = {s: 1 / by_sel[s] for s in needed}
            total = sum(inv.values())
            for s in needed:
                per_sel[_key(market, s, line, player)].append(inv[s] / total)
    return {k: (median(v), len(v)) for k, v in per_sel.items()}


def price_predictions(predictions: list[dict[str, Any]], quotes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    names = _resolve_players(predictions, quotes)
    quotes = [{**q, "player": names.get(q["player"], q["player"]) if q.get("player") else None} for q in quotes]

    by_key: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for q in quotes:
        by_key[_key(q["market"], q["selection"], q["line"], q["player"])].append(q)
    consensus = _market_probs(quotes)

    out = []
    for p in predictions:
        k = _key(p["market"], p["selection"], p["line"], p["player"])
        qs = by_key.get(k, [])
        allowed = [q for q in qs if bookmaker_allowed(q["bookmaker"])]
        best = max(allowed, key=lambda q: q["price"]) if allowed else None
        mp = consensus.get(k)
        row = {
            **p,
            "best_odds": best["price"] if best else None,
            "bookmaker": best["bookmaker"] if best else None,
            "prices": sorted(({"bookmaker": q["bookmaker"], "odds": q["price"]} for q in qs), key=lambda x: -x["odds"]),
            "market_prob": round(mp[0], 4) if mp else None,
            "market_books": mp[1] if mp else 0,
            "implied_prob": round(1 / best["price"], 4) if best else None,
        }
        if best:
            model_p = p["probability"]
            if mp:
                w = 0.5 + 0.25 * p["reliability"]  # 0.5 .. 0.75 weight on the model
                blended = w * model_p + (1 - w) * mp[0]
                score = blended * best["price"] - 1
            else:
                blended = model_p
                score = (model_p * best["price"] - 1) * ONE_SIDED_PENALTY * (0.5 + 0.5 * p["reliability"])
            ev = model_p * best["price"] - 1
            b = best["price"] - 1
            kelly = max((blended * best["price"] - 1) / b, 0.0) if b > 0 else 0.0
            if not mp:
                kelly *= ONE_SIDED_PENALTY  # no market sanity check: stake less
            row.update(
                {
                    "blended_prob": round(blended, 4),
                    "ev": round(ev, 4),
                    "value_score": round(score, 4),
                    "quarter_kelly_stake_pct": round(100 * kelly / 4, 2),
                }
            )
        else:
            row.update({"blended_prob": None, "ev": None, "value_score": None, "quarter_kelly_stake_pct": None})
        out.append(row)
    return out


def _family(p: dict[str, Any]) -> tuple:
    # Over 9.5 and Over 10.5 corners are the same bet at different prices; show only the best one.
    return (p["market"], p["selection"], p["player"])


def top_value(priced: list[dict[str, Any]], n: int = 10) -> list[dict[str, Any]]:
    candidates = [
        p for p in priced
        if p.get("value_score") is not None
        and p["value_score"] > 0
        and p["ev"] > 0
        and MIN_ODDS <= p["best_odds"] <= MAX_ODDS
    ]
    candidates.sort(key=lambda p: -p["value_score"])
    seen_family: set[tuple] = set()
    per_player: dict[str, int] = defaultdict(int)
    out = []
    for p in candidates:
        fam = _family(p)
        if fam in seen_family:
            continue
        if p["player"] and per_player[p["player"]] >= MAX_PER_PLAYER:
            continue
        seen_family.add(fam)
        if p["player"]:
            per_player[p["player"]] += 1
        out.append(p)
        if len(out) >= n:
            break
    return out
