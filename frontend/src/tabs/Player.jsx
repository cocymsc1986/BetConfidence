import { useEffect, useState } from 'react';
import { api } from '../api.js';
import BetCard from '../components/BetCard.jsx';

export default function Player() {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState([]);
  const [selected, setSelected] = useState(null);
  const [stats, setStats] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (query.length < 2) {
      setResults([]);
      return;
    }
    const t = setTimeout(() => {
      api.searchPlayers(query).then((d) => setResults(d.results || [])).catch((e) => setError(e.message));
    }, 250);
    return () => clearTimeout(t);
  }, [query]);

  useEffect(() => {
    if (!selected) return;
    setStats(null);
    api.playerStats(selected.id).then(setStats).catch((e) => setError(e.message));
  }, [selected]);

  return (
    <div>
      <div className="filters">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search player…"
          style={{ flex: 1 }}
        />
      </div>

      {!selected && (
        <div className="card">
          {results.length === 0 ? (
            <div className="empty">Start typing to search.</div>
          ) : (
            results.map((p) => (
              <div
                key={p.id}
                style={{ padding: '8px 0', cursor: 'pointer', borderBottom: '1px solid var(--border)' }}
                onClick={() => setSelected(p)}
              >
                <strong>{p.name}</strong> · <span style={{ color: 'var(--muted)' }}>{p.team_name} · {p.position || '?'}</span>
              </div>
            ))
          )}
        </div>
      )}

      {selected && (
        <>
          <div className="card" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <h2 style={{ margin: 0 }}>{selected.name}</h2>
              <div style={{ color: 'var(--muted)' }}>{selected.team_name} · {selected.position}</div>
            </div>
            <button className="link" style={{ background: 'transparent', border: 0 }} onClick={() => { setSelected(null); setStats(null); }}>
              ← back
            </button>
          </div>

          {!stats && <div className="empty">Loading…</div>}

          {stats?.signals?.length > 0 && (
            <>
              <h3>Active Signals</h3>
              {stats.signals.map((s, i) => <BetCard key={i} signal={s} />)}
            </>
          )}

          {stats?.matches?.length > 0 && (
            <>
              <h3>Match History</h3>
              <div className="card">
                <table>
                  <thead>
                    <tr>
                      <th>Date</th>
                      <th>Match</th>
                      <th>Min</th>
                      <th>G</th>
                      <th>A</th>
                      <th>SoT</th>
                      <th>Sh</th>
                      <th>FC</th>
                      <th>FD</th>
                      <th>YC</th>
                      <th>RC</th>
                    </tr>
                  </thead>
                  <tbody>
                    {stats.matches.map((m) => (
                      <tr key={m.match_id}>
                        <td>{m.date?.slice(0, 10)}</td>
                        <td>{m.home_name} vs {m.away_name}</td>
                        <td>{m.minutes_played}</td>
                        <td>{m.goals}</td>
                        <td>{m.assists}</td>
                        <td>{m.shots_on_target}</td>
                        <td>{m.shots_total}</td>
                        <td>{m.fouls_committed}</td>
                        <td>{m.fouls_drawn}</td>
                        <td>{m.yellow_card}</td>
                        <td>{m.red_card}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}

          {error && <div className="empty">Error: {error}</div>}
        </>
      )}
    </div>
  );
}
