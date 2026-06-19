const BASE = '/api';

async function jsonFetch(path, options) {
  const res = await fetch(`${BASE}${path}`, options);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export const api = {
  signals: (date = 'today') => jsonFetch(`/signals?match_date=${date}`),
  bestSignals: (date = 'today') => jsonFetch(`/signals/best?match_date=${date}`),
  upcoming: (days = 7) => jsonFetch(`/matches/upcoming?days=${days}`),
  searchPlayers: (q) => jsonFetch(`/players/search?q=${encodeURIComponent(q)}`),
  playerStats: (id) => jsonFetch(`/player/${id}/stats`),
  debugStatus: () => jsonFetch('/debug/status'),
  ingestRefresh: ({ daysBack = 365, daysForward = 7, fetchStats = true } = {}) =>
    jsonFetch(
      `/ingest/refresh?days_back=${daysBack}&days_forward=${daysForward}&fetch_stats=${fetchStats}`,
      { method: 'POST' },
    ),
};
