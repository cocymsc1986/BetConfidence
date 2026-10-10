"""Bookmaker odds from two sources, normalised into canonical markets.

Sources
-------
1. API-Football ``/odds?fixture=ID`` — same key as the stats feed. Pre-match odds
   from ~15 bookmakers (Bet365, William Hill, Betfair, Unibet, Pinnacle, ...)
   including corners, cards and goalscorer markets. Refreshed by the provider
   every few hours, available 1-14 days before kick-off.
2. The Odds API (``ODDS_API_KEY``) — UK/EU bookmakers (William Hill, Paddy Power,
   Sky Bet, Betfair, Ladbrokes, ...). Featured markets (h2h, totals) come from
   the bulk endpoint; BTTS, corners, cards and player props are *additional
   markets* that The Odds API only serves one event at a time via
   ``/events/{id}/odds`` — asking for them on the bulk endpoint rejects the
   whole request, which is why the old implementation never cached any odds.

Every quote is stored per bookmaker (``odds_quotes``) so the analysis can pick
the best price and build a de-margined market consensus.
"""
from __future__ import annotations

import logging
import os
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx

from . import db, ingest

log = logging.getLogger("pitch_edge.odds")
if not log.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("[odds] %(message)s"))
    log.addHandler(h)
log.setLevel(logging.INFO)


ODDS_HOST = "https://api.the-odds-api.com/v4"
ODDS_KEY = os.getenv("ODDS_API_KEY", "")
ODDS_REGIONS = os.getenv("ODDS_API_REGIONS", "uk,eu")

# The Odds API uses sport_keys, mapped from our league ids.
SPORT_KEYS = {
    39: "soccer_epl",
    40: "soccer_efl_champ",
    140: "soccer_spain_la_liga",
    135: "soccer_italy_serie_a",
    78: "soccer_germany_bundesliga",
    61: "soccer_france_ligue_one",
    2: "soccer_uefa_champs_league",
    3: "soccer_uefa_europa_league",
    1: "soccer_fifa_world_cup",
    4: "soccer_uefa_european_championship",
    5: "soccer_uefa_nations_league",
    9: "soccer_conmebol_copa_america",
    13: "soccer_conmebol_copa_libertadores",
    15: "soccer_fifa_club_world_cup",
}

TARGET_LEAGUES = [int(x) for x in os.getenv("TARGET_LEAGUES", "39").split(",") if x.strip()]

BULK_MARKETS = ["h2h", "totals"]  # featured markets only — anything else 422s the bulk call
EVENT_MARKETS = [
    m.strip()
    for m in os.getenv(
        "ODDS_API_EVENT_MARKETS",
        "btts,alternate_totals,alternate_totals_corners,alternate_totals_cards,"
        "player_goal_scorer_anytime,player_shots_on_target,player_shots,player_to_receive_card,player_assists",
    ).split(",")
    if m.strip()
]

# Bookmakers whose prices are eligible for "best price". Consensus probability
# still uses every bookmaker fetched. Empty env value = allow all.
DEFAULT_BOOKMAKERS = "bet365,williamhill,paddypower,skybet,betfair,ladbrokes,coral,unibet,betway,betvictor"
ALLOWED_BOOKMAKERS = {
    b for b in (x.strip() for x in os.getenv("ODDS_BOOKMAKERS", DEFAULT_BOOKMAKERS).split(",")) if b
}


# ---------------------------------------------------------------- names

_BOOK_ALIASES = {
    "betfairexuk": "betfair_exchange",
    "betfairexeu": "betfair_exchange",
    "betfairex": "betfair_exchange",
    "betfairexchange": "betfair_exchange",
    "betfairsbuk": "betfair",
    "betfairsportsbook": "betfair",
    "williamhill": "williamhill",
    "ladbrokesuk": "ladbrokes",
    "unibetuk": "unibet",
    "unibeteu": "unibet",
    "unibetnl": "unibet",
    "sport888": "888sport",
    "onexbet": "1xbet",
}


def bookmaker_key(name: str) -> str:
    k = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    if k in _BOOK_ALIASES:
        return _BOOK_ALIASES[k]
    return re.sub(r"(uk|eu|us)$", "", k) or k


def bookmaker_allowed(name: str) -> bool:
    return not ALLOWED_BOOKMAKERS or bookmaker_key(name) in {bookmaker_key(b) for b in ALLOWED_BOOKMAKERS}


_NORMALISE_DROP = re.compile(r"\b(fc|cf|afc|sc|ac|club|de|the)\b|[^\w\s]", re.IGNORECASE)


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c))


def _normalise(name: str) -> str:
    """Strip common suffixes/punctuation so 'Manchester United FC' ≈ 'Man United'."""
    n = _NORMALISE_DROP.sub(" ", _strip_accents(name)).lower()
    return " ".join(n.split())


def _token_overlap(a: str, b: str) -> float:
    ta, tb = set(_normalise(a).split()), set(_normalise(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta), len(tb))


def _team_similarity(a: str, b: str) -> float:
    na, nb = _normalise(a), _normalise(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    score = _token_overlap(a, b)
    # "Man Utd" vs "Manchester United", "Wolves" vs "Wolverhampton": prefix match on tokens.
    ta, tb = na.split(), nb.split()
    prefix = sum(1 for x in ta if any(y.startswith(x[:3]) and len(x) >= 3 for y in tb)) / max(len(ta), len(tb))
    return max(score, 0.8 * prefix)


def player_name_match(a: str, b: str) -> bool:
    """'E. Haaland' ~ 'Erling Haaland' ~ 'Erling Braut Haaland'."""
    ta = re.sub(r"[^a-z\s]", " ", _strip_accents(a).lower().replace(".", " ")).split()
    tb = re.sub(r"[^a-z\s]", " ", _strip_accents(b).lower().replace(".", " ")).split()
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    if ta[-1] != tb[-1]:
        return False
    if len(ta) == 1 or len(tb) == 1:
        return True  # single-name players ("Rodri") or surname-only listings
    return ta[0][0] == tb[0][0]


# ---------------------------------------------------------------- quotes

def _quote(bookmaker: str, market: str, selection: str, line: float | None, price: Any, player: str | None = None):
    try:
        p = float(price)
    except (TypeError, ValueError):
        return None
    if p <= 1.0:
        return None
    return {
        "bookmaker": bookmaker_key(bookmaker),
        "market": market,
        "selection": selection,
        "line": None if line is None else float(line),
        "player": player,
        "price": p,
    }


_OU_RE = re.compile(r"^\s*(over|under)\s+([0-9]+(?:\.[0-9]+)?)\s*$", re.IGNORECASE)
_PLAYER_OU_RE = re.compile(r"^\s*(.+?)\s*[-:–]?\s*(over|under)\s+([0-9]+(?:\.[0-9]+)?)\s*$", re.IGNORECASE)

# API-Football bet names (lower-cased) -> (canonical market, kind)
# kind: "1x2" | "ou" | "yn" | "player_yes" | "player_ou"
_AF_BETS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"^match winner$"), "match_result", "1x2"),
    (re.compile(r"^goals over/under$"), "total_goals", "ou"),
    (re.compile(r"^both teams (to )?score$"), "btts", "yn"),
    (re.compile(r"^total - home$"), "home_total_goals", "ou"),
    (re.compile(r"^total - away$"), "away_total_goals", "ou"),
    (re.compile(r"^corners over[/ ]?under$"), "total_corners", "ou"),
    (re.compile(r"^total corners( \(3 way\))?$"), "total_corners", "ou"),
    (re.compile(r"^home corners over[/ ]?under$"), "home_total_corners", "ou"),
    (re.compile(r"^away corners over[/ ]?under$"), "away_total_corners", "ou"),
    (re.compile(r"^cards over[/ ]?under$"), "total_cards", "ou"),
    (re.compile(r"^anytime goal ?scorer$"), "player_goal", "player_yes"),
    (re.compile(r"^player to be booked$|^player (to receive a )?card$|^anytime booked$"), "player_card", "player_yes"),
    (re.compile(r"^player (to )?assist$|^anytime assist$"), "player_assist", "player_yes"),
    (re.compile(r"^player shots on target"), "player_shots_on_target", "player_ou"),
    (re.compile(r"^player (total )?shots(?! on)"), "player_shots", "player_ou"),
]


def normalise_api_football(response: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Flatten API-Football /odds response into canonical quotes. Returns (quotes, unmapped_bet_names)."""
    quotes: list[dict[str, Any]] = []
    unmapped: set[str] = set()
    for item in response or []:
        for book in item.get("bookmakers") or []:
            bname = book.get("name") or str(book.get("id"))
            for bet in book.get("bets") or []:
                name = (bet.get("name") or "").strip().lower()
                spec = next(((m, k) for rx, m, k in _AF_BETS if rx.search(name)), None)
                if not spec:
                    unmapped.add(bet.get("name") or "?")
                    continue
                market, kind = spec
                for v in bet.get("values") or []:
                    val = str(v.get("value", "")).strip()
                    price = v.get("odd")
                    q = None
                    if kind == "1x2":
                        sel = {"home": "home", "draw": "draw", "away": "away"}.get(val.lower())
                        q = sel and _quote(bname, market, sel, None, price)
                    elif kind == "ou":
                        m = _OU_RE.match(val)
                        if m:
                            q = _quote(bname, market, m.group(1).lower(), float(m.group(2)), price)
                    elif kind == "yn":
                        if val.lower() in ("yes", "no"):
                            q = _quote(bname, market, val.lower(), None, price)
                    elif kind == "player_yes":
                        if val and val.lower() not in ("no goalscorer", "no", "yes"):
                            q = _quote(bname, market, "yes", None, price, player=val)
                    elif kind == "player_ou":
                        m = _PLAYER_OU_RE.match(val)
                        if m:
                            q = _quote(bname, market, m.group(2).lower(), float(m.group(3)), price, player=m.group(1))
                    if q:
                        quotes.append(q)
    return quotes, sorted(unmapped)


# The Odds API market key -> canonical market
_TOA_MARKETS = {
    "h2h": "match_result",
    "totals": "total_goals",
    "alternate_totals": "total_goals",
    "btts": "btts",
    "alternate_totals_corners": "total_corners",
    "totals_corners": "total_corners",
    "alternate_totals_cards": "total_cards",
    "totals_cards": "total_cards",
    "player_goal_scorer_anytime": "player_goal",
    "player_to_receive_card": "player_card",
    "player_assists": "player_assist",
    "player_shots": "player_shots",
    "player_shots_on_target": "player_shots_on_target",
}


def normalise_odds_api_event(ev: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten one The Odds API event (bulk or per-event response) into canonical quotes."""
    home, away = ev.get("home_team", ""), ev.get("away_team", "")
    quotes: list[dict[str, Any]] = []
    for book in ev.get("bookmakers") or []:
        bname = book.get("key") or book.get("title") or "?"
        for mkt in book.get("markets") or []:
            market = _TOA_MARKETS.get(mkt.get("key"))
            if not market:
                continue
            for out in mkt.get("outcomes") or []:
                name = str(out.get("name", ""))
                price = out.get("price")
                point = out.get("point")
                player = out.get("description")
                q = None
                if market == "match_result":
                    if name.lower() == "draw":
                        sel = "draw"
                    elif _team_similarity(name, home) >= _team_similarity(name, away):
                        sel = "home"
                    else:
                        sel = "away"
                    q = _quote(bname, market, sel, None, price)
                elif market == "btts":
                    if name.lower() in ("yes", "no"):
                        q = _quote(bname, market, name.lower(), None, price)
                elif market in ("player_goal", "player_card"):
                    if player and name.lower() == "yes":
                        q = _quote(bname, market, "yes", None, price, player=player)
                elif market == "player_assist":
                    # Offered as "Over 0.5" assists; equivalent to "to assist".
                    if player and name.lower() in ("over", "yes") and (point is None or float(point) == 0.5):
                        q = _quote(bname, market, "yes", None, price, player=player)
                elif market.startswith("player_"):
                    if player and name.lower() in ("over", "under") and point is not None:
                        q = _quote(bname, market, name.lower(), point, price, player=player)
                else:  # over/under totals
                    if name.lower() in ("over", "under") and point is not None:
                        q = _quote(bname, market, name.lower(), point, price)
                if q:
                    quotes.append(q)
    return quotes


def store_quotes(match_id: int, source: str, quotes: Iterable[dict[str, Any]], *, replace: bool = True) -> int:
    now = datetime.now(timezone.utc).isoformat()
    n = 0
    with db.get_conn() as conn:
        if replace:
            conn.execute("DELETE FROM odds_quotes WHERE match_id = ? AND source = ?", (match_id, source))
        for q in quotes:
            db.upsert(
                conn,
                "odds_quotes",
                {
                    "match_id": match_id,
                    "source": source,
                    "bookmaker": q["bookmaker"],
                    "market": q["market"],
                    "selection": q["selection"],
                    "line": db.NO_LINE if q["line"] is None else q["line"],
                    "player": q.get("player") or "",
                    "price": q["price"],
                    "updated_at": now,
                },
                keys=("match_id", "source", "bookmaker", "market", "selection", "line", "player"),
            )
            n += 1
    return n


# ---------------------------------------------------------------- fixture matching

def _match_id_from_event(conn, home_name: str, away_name: str, commence: str | None) -> int | None:
    rows = list(
        conn.execute(
            """
            SELECT m.id, m.date, ht.name AS home, at.name AS away
            FROM matches m
            JOIN teams ht ON ht.id = m.home_team
            JOIN teams at ON at.id = m.away_team
            WHERE date(m.date) >= date('now', '-1 day')
              AND date(m.date) <= date('now', '+21 day')
            """
        )
    )
    when = _parse_ts(commence)
    best: tuple[float, int | None] = (0.0, None)
    for r in rows:
        if when is not None:
            kickoff = _parse_ts(r["date"])
            if kickoff is not None and abs((kickoff - when).total_seconds()) > 12 * 3600:
                continue
        score = (_team_similarity(home_name, r["home"]) + _team_similarity(away_name, r["away"])) / 2
        if score > best[0]:
            best = (score, int(r["id"]))
    # Require a reasonable confidence so we don't bind odds to the wrong fixture.
    return best[1] if best[0] >= 0.5 else None


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- The Odds API

def _toa_get(client: httpx.Client, path: str, params: dict[str, Any]) -> Any:
    resp = client.get(f"{ODDS_HOST}{path}", params={"apiKey": ODDS_KEY, **params}, timeout=30.0)
    if resp.status_code >= 400:
        raise httpx.HTTPStatusError(f"{resp.status_code} {resp.text[:200]}", request=resp.request, response=resp)
    remaining = resp.headers.get("x-requests-remaining")
    if remaining is not None:
        log.info("The Odds API credits remaining: %s", remaining)
    return resp.json()


def refresh_match_odds() -> dict[str, Any]:
    """Bulk refresh of featured markets (1X2, goals O/U) for every target league. Cheap: 2 credits/region/league."""
    if not ODDS_KEY:
        return {"ok": False, "error": "ODDS_API_KEY not set — check .env"}

    db.init_db()
    pulled = skipped = 0
    errors: list[str] = []
    with httpx.Client() as client:
        for league_id in TARGET_LEAGUES:
            sport_key = SPORT_KEYS.get(league_id)
            if not sport_key:
                errors.append(f"no Odds API sport_key mapped for league {league_id}")
                continue
            try:
                events = _toa_get(
                    client,
                    f"/sports/{sport_key}/odds",
                    {"regions": ODDS_REGIONS, "markets": ",".join(BULK_MARKETS), "oddsFormat": "decimal"},
                )
            except httpx.HTTPError as e:
                errors.append(f"{sport_key}: {e}")
                continue
            log.info("sport=%s returned %d events", sport_key, len(events))
            for ev in events:
                with db.get_conn() as conn:
                    match_id = _match_id_from_event(conn, ev.get("home_team", ""), ev.get("away_team", ""), ev.get("commence_time"))
                if not match_id:
                    skipped += 1
                    continue
                pulled += store_quotes(match_id, "the-odds-api:bulk", normalise_odds_api_event(ev))
    return {"ok": not errors or pulled > 0, "odds_rows": pulled, "skipped_events": skipped, "errors": errors}


def _refresh_odds_api_event(client: httpx.Client, match: Any) -> dict[str, Any]:
    sport_key = SPORT_KEYS.get(int(match["league"]))
    if not sport_key:
        return {"ok": False, "error": f"no Odds API sport_key mapped for league {match['league']}"}
    events = _toa_get(client, f"/sports/{sport_key}/events", {})  # free: costs no credits
    kickoff = _parse_ts(match["date"])
    best, best_score = None, 0.0
    for ev in events:
        when = _parse_ts(ev.get("commence_time"))
        if kickoff and when and abs((kickoff - when).total_seconds()) > 12 * 3600:
            continue
        score = (_team_similarity(ev.get("home_team", ""), match["home_name"]) + _team_similarity(ev.get("away_team", ""), match["away_name"])) / 2
        if score > best_score:
            best, best_score = ev, score
    if not best or best_score < 0.5:
        return {"ok": False, "error": "event not found on The Odds API"}

    quotes: list[dict[str, Any]] = []
    errors: list[str] = []
    # Request in one go; if the provider rejects a market for this sport, fall back to one-by-one.
    markets = ["h2h", "totals", *EVENT_MARKETS]
    try:
        ev = _toa_get(
            client,
            f"/sports/{sport_key}/events/{best['id']}/odds",
            {"regions": ODDS_REGIONS, "markets": ",".join(markets), "oddsFormat": "decimal"},
        )
        quotes = normalise_odds_api_event(ev)
    except httpx.HTTPStatusError as e:
        if e.response is None or e.response.status_code != 422:
            raise
        for mk in markets:
            try:
                ev = _toa_get(
                    client,
                    f"/sports/{sport_key}/events/{best['id']}/odds",
                    {"regions": ODDS_REGIONS, "markets": mk, "oddsFormat": "decimal"},
                )
                quotes += normalise_odds_api_event(ev)
            except httpx.HTTPStatusError as e2:
                errors.append(f"{mk}: {e2}")
    n = store_quotes(int(match["id"]), "the-odds-api", quotes)
    return {"ok": True, "event_id": best["id"], "quotes": n, "errors": errors}


# ---------------------------------------------------------------- API-Football odds

def _refresh_api_football(client: httpx.Client, match_id: int) -> dict[str, Any]:
    response: list[dict[str, Any]] = []
    page, total = 1, 1
    while page <= total and page <= 10:
        body = ingest._get(client, "/odds", {"fixture": match_id, "page": page})
        response += body.get("response", [])
        total = int((body.get("paging") or {}).get("total") or 1)
        page += 1
    quotes, unmapped = normalise_api_football(response)
    n = store_quotes(match_id, "api-football", quotes)
    return {"ok": True, "quotes": n, "unmapped_bets": unmapped}


def refresh_odds_for_match(match_id: int) -> dict[str, Any]:
    """Pull every available market for one fixture from both sources."""
    db.init_db()
    match = db.get_match(match_id)
    if match is None:
        return {"ok": False, "error": f"match {match_id} not found"}
    out: dict[str, Any] = {}
    with httpx.Client() as client:
        if ingest.API_KEY:
            try:
                out["api_football"] = _refresh_api_football(client, match_id)
            except (httpx.HTTPError, ingest.APIFootballError) as e:
                out["api_football"] = {"ok": False, "error": str(e)}
        else:
            out["api_football"] = {"ok": False, "error": "API_FOOTBALL_KEY not set"}
        if ODDS_KEY:
            try:
                out["the_odds_api"] = _refresh_odds_api_event(client, match)
            except httpx.HTTPError as e:
                out["the_odds_api"] = {"ok": False, "error": str(e)}
        else:
            out["the_odds_api"] = {"ok": False, "error": "ODDS_API_KEY not set"}
    return out
