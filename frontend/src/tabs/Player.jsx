import { useEffect, useState } from 'react';
import { api } from '../api.js';
import BetCard from '../components/BetCard.jsx';
import { SortTh, useSort } from '../components/Sortable.jsx';

const HISTORY_COLUMNS = [
  ['date', 'Date', (m) => m.date],
  ['match', 'Match', (m) => `${m.home_name} vs ${m.away_name}`],
  ['minutes_played', 'Min'],
  ['goals', 'G'],
  ['assists', 'A'],
  ['shots_on_target', 'SoT'],
  ['shots_total', 'Sh'],
  ['fouls_committed', 'FC'],
  ['fouls_drawn', 'FD'],
  ['yellow_card', 'YC'],
  ['red_card', 'RC'],
];
const HISTORY_SORT = Object.fromEntries(HISTORY_COLUMNS.map(([k, , get]) => [k, get || ((m) => m[k])]));

function MatchHistory({ matches }) {
  const { sorted, sort, toggle } = useSort(matches, HISTORY_SORT);
  return (
    <div className="card table-scroll">
      <table>
        <thead>
          <tr>
            {HISTORY_COLUMNS.map(([k, label]) => (
              <SortTh key={k} col={k} sort={sort} onSort={toggle}>{label}</SortTh>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((m) => (
            <tr key={m.match_id}>
              <td>{m.date?.slice(0, 10)}</td>
              <td>{m.home_name} vs {m.away_name}</td>
              {HISTORY_COLUMNS.slice(2).map(([k]) => (
                <td key={k}>{m[k]}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

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
              <MatchHistory matches={stats.matches} />
            </>
          )}

          {error && <div className="empty">Error: {error}</div>}
        </>
      )}
    </div>
  );
}
