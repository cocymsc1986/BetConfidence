import { useEffect, useState } from 'react';
import { api } from '../api.js';
import MatchAnalysis from '../components/MatchAnalysis.jsx';
import { SortTh, useSort } from '../components/Sortable.jsx';

const COLUMNS = {
  date: (m) => m.date,
  match: (m) => `${m.home_name} vs ${m.away_name}`,
  referee: (m) => m.referee_name,
  home_win: (m) => m.odds?.home_win,
  draw: (m) => m.odds?.draw,
  away_win: (m) => m.odds?.away_win,
  over_2_5: (m) => m.odds?.over_2_5,
  btts_yes: (m) => m.odds?.btts_yes,
};

export default function Fixtures() {
  const [matches, setMatches] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(null);
  const { sorted, sort, toggle } = useSort(matches, COLUMNS);

  useEffect(() => {
    api
      .upcoming(7)
      .then((d) => setMatches(d.matches || []))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (selected) return <MatchAnalysis match={selected} onBack={() => setSelected(null)} />;
  if (loading) return <div className="empty">Loading fixtures…</div>;
  if (error) return <div className="empty">Error: {error}</div>;
  if (!matches.length) return <div className="empty">No upcoming fixtures cached. Run a refresh from the Admin tab.</div>;

  return (
    <div className="card table-scroll">
      <table>
        <thead>
          <tr>
            <SortTh col="date" sort={sort} onSort={toggle}>Date</SortTh>
            <SortTh col="match" sort={sort} onSort={toggle}>Match</SortTh>
            <SortTh col="referee" sort={sort} onSort={toggle}>Referee</SortTh>
            <SortTh col="home_win" sort={sort} onSort={toggle}>Home</SortTh>
            <SortTh col="draw" sort={sort} onSort={toggle}>Draw</SortTh>
            <SortTh col="away_win" sort={sort} onSort={toggle}>Away</SortTh>
            <SortTh col="over_2_5" sort={sort} onSort={toggle}>O2.5</SortTh>
            <SortTh col="btts_yes" sort={sort} onSort={toggle}>BTTS</SortTh>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((m) => (
            <tr key={m.id}>
              <td>{m.date?.slice(0, 16).replace('T', ' ')}</td>
              <td>{m.home_name} vs {m.away_name}</td>
              <td>{m.referee_name || '—'}</td>
              <td>{m.odds?.home_win?.toFixed(2) ?? '—'}</td>
              <td>{m.odds?.draw?.toFixed(2) ?? '—'}</td>
              <td>{m.odds?.away_win?.toFixed(2) ?? '—'}</td>
              <td>{m.odds?.over_2_5?.toFixed(2) ?? '—'}</td>
              <td>{m.odds?.btts_yes?.toFixed(2) ?? '—'}</td>
              <td>
                <button className="primary" onClick={() => setSelected(m)}>Analyse</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
