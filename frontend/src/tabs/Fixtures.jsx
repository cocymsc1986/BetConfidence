import { useEffect, useState } from 'react';
import { api } from '../api.js';

export default function Fixtures() {
  const [matches, setMatches] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    api
      .upcoming(7)
      .then((d) => setMatches(d.matches || []))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="empty">Loading fixtures…</div>;
  if (error) return <div className="empty">Error: {error}</div>;
  if (!matches.length) return <div className="empty">No upcoming fixtures cached. Run /ingest/refresh.</div>;

  return (
    <div className="card">
      <table>
        <thead>
          <tr>
            <th>Date</th>
            <th>Match</th>
            <th>Referee</th>
            <th>Home</th>
            <th>Draw</th>
            <th>Away</th>
            <th>O2.5</th>
            <th>BTTS</th>
          </tr>
        </thead>
        <tbody>
          {matches.map((m) => (
            <tr key={m.id}>
              <td>{m.date?.slice(0, 16).replace('T', ' ')}</td>
              <td>{m.home_name} vs {m.away_name}</td>
              <td>{m.referee_name || '—'}</td>
              <td>{m.odds?.home_win?.toFixed(2) ?? '—'}</td>
              <td>{m.odds?.draw?.toFixed(2) ?? '—'}</td>
              <td>{m.odds?.away_win?.toFixed(2) ?? '—'}</td>
              <td>{m.odds?.over_2_5?.toFixed(2) ?? '—'}</td>
              <td>{m.odds?.btts_yes?.toFixed(2) ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
