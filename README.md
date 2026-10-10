# Pitch Edge

A football bet advisor. Pick an upcoming fixture, click **Analyse**, and it:

1. pulls both teams' latest matches in any competition (up to the day before), plus confirmed lineups once they're published,
2. pulls current prices from multiple bookmakers,
3. models probabilities for ~200 markets (result, goals, BTTS, corners, cards, and player shots, shots on target, scorer, assist and card),
4. returns the **top 10 value bets**, each with the model's chance, the bookmakers' consensus, the fair odds, the best available price and the edge.

## Stack
- Backend: Python + FastAPI + SQLite (raw `sqlite3`)
- Frontend: React + Vite
- Data: [API-Football](https://www.api-football.com/) (stats + odds) and [The Odds API](https://the-odds-api.com/) (UK/EU bookmaker odds)

## Data sources

| Need | Source | Notes |
| --- | --- | --- |
| Fixtures, results, referee | API-Football `/fixtures` | Live-updated; finished games show up within minutes. |
| Team stats per match (corners, shots, fouls, cards) | API-Football `/fixtures/statistics` | |
| Player stats per match | API-Football `/fixtures/players` | |
| Lineups | API-Football `/fixtures/lineups` | Usually 20–40 min before kick-off. Starters/bench override the minutes model. |
| Odds: Bet365, William Hill, Betfair, Unibet, Pinnacle, … | API-Football `/odds?fixture=` | Includes corners, cards and goalscorer markets. |
| Odds: William Hill, Paddy Power, Sky Bet, Ladbrokes, Betfair, … | The Odds API | 1X2 and totals in bulk; BTTS, corners, cards and player props per event. |

**Plans.** API-Football's free plan has 100 requests/day, and its season coverage may be limited, so check with your key. A first
analysis of a match costs roughly `4 + 2 × (uncached recent matches)` calls, so up to ~80 for two teams. Re-running it costs about 5.
The Pro plan (~$19/month, 7,500/day) is realistic for daily use. The Odds API's free plan has 500 credits/month. Each per-match
pull costs about `markets × regions` credits (~22 with the defaults), so lower `ODDS_API_EVENT_MARKETS` / `ODDS_API_REGIONS`
to save credits.

## Daily paper picks on the free plans

`python -m backend.daily` ranks the day's best bets across every fixture in `TARGET_LEAGUES`: model probability
above 50% and positive edge, top 10, printed as a table (`--json picks.json` saves the full result). It places
no bets. It is built to run once a day on the free plans:

| Plan | Allowance | What a daily run spends |
| --- | --- | --- |
| API-Football free | 100 calls/day | 2 per league for fixtures, 1–2 per league for every bookmaker's odds on the day's games, then the rest on match stats (2 per match, newest first) |
| The Odds API free | 500 credits/month | 2 per league with `ODDS_API_REGIONS=uk` (bulk 1X2 + goals only), so about 60/month per league |

Settings for the free plans:

```bash
API_FOOTBALL_DAILY_BUDGET=95      # stop before the 100/day limit
API_FOOTBALL_MIN_INTERVAL=6       # 10 calls/minute
ODDS_API_REGIONS=uk
ODDS_API_MIN_REMAINING=20
PITCH_EDGE_DB=/somewhere/persistent/pitch_edge.db
```

What the free run gives up compared with **Analyse**: no per-match team history from cups and Europe (league
matches only), no lineups, and no per-event Odds API markets (BTTS, corners, cards, player props still come from
API-Football's bulk odds). Stats history fills in over the first days: a year of one league is about 380
matches, about 45 matches a day at 2 calls each, so roughly 9 days, and longer with more leagues. Picks before that
use whatever history has arrived. Keep the database file between runs or every run starts again from nothing.
Whether the free API-Football key covers the current season is worth checking with `/debug/status` after a run.

## Setup

```bash
cp .env.example .env               # fill in API_FOOTBALL_KEY and ODDS_API_KEY

# backend (run from the repo root)
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload --port 8000

# frontend
cd frontend && npm install && npm run dev   # http://localhost:5173
```

### Or in VS Code
1. Open the folder and install the recommended Python extensions when prompted.
2. **Terminal → Run Task → Setup: install dependencies**. This creates `.venv`, installs everything and copies `.env.example` to `.env`. Then add your API keys to `.env`.
3. **Run and Debug → "Pitch Edge: full stack" → F5**. This starts the backend with the debugger attached plus the frontend, and opens the browser.
   - Other launch options: `Backend (FastAPI)`, `Frontend (Vite)`, `Tests (pytest)`.
   - Tests also show in the Testing sidebar. The **Refresh data** task calls `/ingest/refresh`.
   - On Windows, if VS Code doesn't pick up the venv, choose `.venv\Scripts\python.exe` via *Python: Select Interpreter*.

Then use **Admin → Refresh** (or `POST /ingest/refresh`) once to load the fixture list and league history. After that, use **Fixtures → Analyse** for each match you care about.

Tests: `pip install pytest && python -m pytest backend/tests`

## API
| Method | Path | Notes |
| --- | --- | --- |
| POST | `/matches/{id}/analyse?top=10` | **One-click analysis**: refreshes stats, lineups and odds for this match, then ranks the bets |
| GET | `/matches/{id}/analysis?top=10` | Same, but uses cached data only (no API calls) |
| GET | `/matches/upcoming?days=7` | Fixtures with the best cached 1X2 / O2.5 / BTTS prices |
| GET | `/signals?match_date=today` | All signals for a date, ranked by value |
| GET | `/signals/best` | Edge > 5%, odds ≥ 1.5, positive after market consensus |
| GET | `/player/{id}/stats` | Match history + props for the player's next fixtures |
| GET | `/players/search?q=` | Player search |
| POST | `/ingest/refresh` | Bulk: fixtures + stats for `TARGET_LEAGUES`, plus bulk 1X2/totals odds |
| GET | `/leagues/discover?q=` | Look up league ids |
| GET | `/debug/status` | Row counts, key status |

## How the analysis works

**Team model** (`backend/model/team.py`). For each metric (goals, corners, cards, shots, shots on target), each team gets an
attack and a defence multiplier. These compare what the team produced or allowed with what an average team would have,
at the same venue and **against the same opponents**. Opponent strength comes from a first pass over every team they've
played. The last 20 matches in all competitions are used, weighted toward recent games (half-life of 10 matches), and
shrunk toward the league average by 5 pseudo-matches so a small sample can't produce extreme numbers. The shrinkage and
opponent adjustment were tuned on simulated seasons with known true strengths, where opponent adjustment cut xG error by about 10%.

- **Goals**: Dixon–Coles bivariate Poisson score matrix, giving 1X2, totals, team totals and BTTS.
- **Corners / cards**: negative binomial. Both counts are over-dispersed relative to Poisson, and the dispersion is estimated from league data.
- **Cards** are multiplied by a **referee factor**: the referee's cards per game, shrunk toward the league average over 8 pseudo-matches.

**Player model** (`backend/model/player.py`):
- Per-90 rates for shots, shots on target, goals, assists and cards, weighted toward recent appearances and shrunk toward a position prior worth 360 minutes.
- Adjusted for the opponent's concession rate and for home/away.
- Minutes are modelled as a start-or-sub mixture from the team's recent matches. Confirmed lineups override this, and players outside the matchday squad are dropped.
- Probabilities are **conditional on the player appearing**, matching how bookmakers void props for non-runners.

**Value** (`backend/value.py`). For each selection the app computes:
- the best price among `ODDS_BOOKMAKERS`;
- the bookmakers' **de-margined consensus** across every bookmaker, including sharp books like Pinnacle, for markets where all outcomes are priced;
- **edge** = model chance × best odds − 1.

Ranking uses a reliability-weighted blend of the model and the market, so a bet that every bookmaker strongly disagrees
with (usually a data problem) doesn't top the list. One-sided props, where no consensus is possible, are discounted. Only
the best line per market is shown, so you won't see Over 9.5 and Over 10.5 corners together, and there are at most two
picks per player. Stake is a quarter-Kelly suggestion.

> Models are estimates. Bookmakers are good at pricing; treat the output as a shortlist to check, not a guarantee. Bet responsibly.

## Configuration (`.env`)
See `.env.example`. Key options: `TARGET_LEAGUES`, `ODDS_BOOKMAKERS` (the books you can actually bet with), `HISTORY_MATCHES`,
`API_FOOTBALL_MIN_INTERVAL`, `ODDS_API_REGIONS` and `ODDS_API_EVENT_MARKETS`.
