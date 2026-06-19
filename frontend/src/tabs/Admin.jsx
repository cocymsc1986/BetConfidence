import { useCallback, useEffect, useState } from 'react';
import { api } from '../api.js';

function StatusGrid({ status }) {
  if (!status) return null;
  const env = status.env || {};
  const rows = [
    ['Teams', status.teams],
    ['Players', status.players],
    ['Matches', status.matches],
    ['Finished (FT)', status.matches_finished],
    ['Matches w/ stats', status.matches_with_stats],
    ['Odds rows', status.odds_cache],
  ];
  return (
    <div className="card">
      <h3>Database</h3>
      <div className="stats-grid">
        {rows.map(([label, val]) => (
          <div key={label}>
            {label}: <span>{val ?? '—'}</span>
          </div>
        ))}
      </div>
      <div className="stats-grid" style={{ marginTop: 4 }}>
        <div>API key set: <span>{env.API_FOOTBALL_KEY_set ? 'yes' : 'no'}</span></div>
        <div>Odds key set: <span>{env.ODDS_API_KEY_set ? 'yes' : 'no'}</span></div>
        <div>Target leagues: <span>{env.TARGET_LEAGUES || '—'}</span></div>
        <div>Active season: <span>{env.active_season ?? '—'}</span></div>
      </div>
      {status.latest_match && (
        <p className="bet-card context" style={{ marginTop: 10 }}>
          Latest match: #{status.latest_match.id} · league {status.latest_match.league} ·{' '}
          {status.latest_match.date?.slice(0, 10)} · {status.latest_match.status}
        </p>
      )}
    </div>
  );
}

function IngestResult({ result }) {
  if (!result) return null;
  const fixtures = result.fixtures || {};
  const odds = result.odds || {};
  const byLeague = fixtures.by_league || {};
  const errors = [...(fixtures.errors || []), ...(odds.errors || [])];
  return (
    <div className="card">
      <h3>Last refresh</h3>
      <div className="stats-grid">
        <div>Fixtures stored: <span>{fixtures.totals?.fixtures ?? '—'}</span></div>
        <div>Player stats pulled: <span>{fixtures.totals?.stats_pulled ?? '—'}</span></div>
        <div>Odds rows: <span>{odds.odds_rows ?? '—'}</span></div>
        <div>Events skipped: <span>{odds.skipped_events ?? '—'}</span></div>
      </div>

      {Object.keys(byLeague).length > 0 && (
        <table style={{ marginTop: 12 }}>
          <thead>
            <tr>
              <th>League</th>
              <th>Kind</th>
              <th>Fixtures</th>
              <th>Stats</th>
              <th>Seasons queried</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(byLeague).map(([id, l]) => (
              <tr key={id}>
                <td>{id}</td>
                <td>{l.kind}</td>
                <td>{l.fixtures}</td>
                <td>{l.stats_pulled}</td>
                <td>
                  {(l.seasons_queried || [])
                    .map((s) => `${s.season} (${s.fixtures})`)
                    .join(', ') || '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {errors.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <p className="pill warn">{errors.length} error(s)</p>
          <ul style={{ margin: '8px 0 0', paddingLeft: 18 }}>
            {errors.map((e, i) => (
              <li key={i} className="bet-card context">{e}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export default function Admin() {
  const [status, setStatus] = useState(null);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const loadStatus = useCallback(() => {
    api.debugStatus().then(setStatus).catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  const runIngest = async () => {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const res = await api.ingestRefresh();
      setResult(res);
      loadStatus();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="best-callout">
        <h2>Admin</h2>
        <p>
          Pull fixtures, player stats and odds from the configured providers into the
          local database. This can take a minute on the first run.
        </p>
      </div>

      <div className="filters">
        <button className="action-btn" onClick={runIngest} disabled={busy}>
          {busy ? 'Refreshing…' : 'Refresh data'}
        </button>
        <button className="action-btn secondary" onClick={loadStatus} disabled={busy}>
          Check status
        </button>
      </div>

      {error && <div className="empty">Error: {error}</div>}

      <IngestResult result={result} />
      <StatusGrid status={status} />
    </>
  );
}
