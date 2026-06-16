# Pitch Edge

A full-stack football bet advisor that ranks markets by expected value.

## Stack
- Backend: Python + FastAPI + SQLite (raw `sqlite3`)
- Frontend: React + Vite
- Data: API-Football (api-sports.io) + The Odds API

## Layout
```
pitch-edge/
├── backend/
│   ├── main.py              # FastAPI app
│   ├── ingest.py            # API-Football data pull
│   ├── odds.py              # The Odds API integration
│   ├── db.py                # SQLite schema + queries
│   ├── signals/
│   │   ├── yellow_card.py
│   │   ├── goalscorer.py
│   │   ├── corners.py
│   │   ├── cards_over.py
│   │   ├── btts.py
│   │   └── engine.py        # Runs all signals, returns ranked list
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.jsx
│   │   ├── tabs/Signals.jsx
│   │   ├── tabs/Fixtures.jsx
│   │   ├── tabs/Player.jsx
│   │   └── components/BetCard.jsx
│   └── package.json
└── .env.example
```

## Setup

1. **Backend**
   ```bash
   cd backend
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   cp ../.env.example ../.env       # then fill in keys
   uvicorn backend.main:app --reload --port 8000
   ```

2. **Frontend**
   ```bash
   cd frontend
   npm install
   npm run dev
   ```

3. **Pull data** (after keys are in `.env`)
   ```bash
   curl -X POST http://localhost:8000/ingest/refresh
   ```

## API
| Method | Path                       | Notes |
| ------ | -------------------------- | ----- |
| GET    | `/signals?match_date=today`| Ranked by EV |
| GET    | `/signals/best`            | Filtered: conf ≥ 0.65, odds ≥ 1.8, EV > 0.05 |
| GET    | `/player/{id}/stats`       | Full history + active signals |
| GET    | `/players/search?q=`       | Player search |
| GET    | `/matches/upcoming?days=7` | Fixtures + cached odds |
| POST   | `/ingest/refresh`          | Pull fixtures, per-player stats, odds |

## Signal engine
Each market calculator returns `{ market, player/match, confidence (0–1), stat_summary }`
and the engine attaches `odds` + `ev_score = confidence * odds − 1`.
Sort order: signals with odds (EV available) first by descending EV, then everything else by confidence.

| Market              | Logic |
| ------------------- | ----- |
| Yellow card         | Booking rate L10 (65/35 recency) × ref card mod × position mod |
| Anytime goalscorer  | Goal rate L10 × position mod |
| Assist              | Assist rate L10 × position mod |
| Over 2.5 goals      | Team scored+conceded averages, home/away split, Poisson tail |
| BTTS                | Mean of both teams' L10 BTTS rate |
| Over 9.5 corners    | Avg total corners (home/away split), Poisson tail |
| Over 3.5 cards      | Referee avg × foul-rate adjustment, Poisson tail |
