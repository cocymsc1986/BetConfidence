import { Fragment, useEffect, useMemo, useState } from 'react';
import { api } from '../api.js';

const pct = (p) => (p == null ? '—' : `${(p * 100).toFixed(1)}%`);
const odds = (o) => (o == null ? '—' : o.toFixed(2));
const signed = (v) => (v == null ? '—' : `${v > 0 ? '+' : ''}${(v * 100).toFixed(1)}%`);
const evClass = (v) => (v == null ? '' : v > 0.05 ? 'pos' : v > 0 ? 'warn-text' : 'neg');

function Detail({ detail }) {
  return (
    <div className="stats-grid">
      {Object.entries(detail || {}).map(([k, v]) => (
        <div key={k}>
          {k.replaceAll('_', ' ')}: <span>{v == null ? '—' : String(v)}</span>
        </div>
      ))}
    </div>
  );
}

function TopBets({ bets }) {
  const [open, setOpen] = useState(null);
  if (!bets.length) return null;
  return (
    <div className="card">
      <h3 style={{ marginTop: 0 }}>Top {bets.length} value bets</h3>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Bet</th>
              <th title="Model probability">Chance</th>
              <th title="Bookmakers' de-margined consensus">Market</th>
              <th>Fair</th>
              <th>Best odds</th>
              <th title="Model chance × best odds − 1">Edge</th>
              <th title="Quarter-Kelly stake as % of bankroll">Stake</th>
            </tr>
          </thead>
          <tbody>
            {bets.map((b, i) => (
              <Fragment key={i}>
                <tr className="clickable" onClick={() => setOpen(open === i ? null : i)}>
                  <td>{i + 1}</td>
                  <td>
                    <strong>{b.label}</strong>
                    {b.reliability < 0.6 && <span className="pill warn small">thin data</span>}
                  </td>
                  <td>{pct(b.probability)}</td>
                  <td>{pct(b.market_prob)}</td>
                  <td>{odds(b.fair_odds)}</td>
                  <td>
                    {odds(b.best_odds)} <span className="muted">{b.bookmaker}</span>
                  </td>
                  <td className={evClass(b.ev)}>{signed(b.ev)}</td>
                  <td>{b.quarter_kelly_stake_pct != null ? `${b.quarter_kelly_stake_pct}%` : '—'}</td>
                </tr>
                {open === i && (
                  <tr>
                    <td colSpan={8}>
                      <Detail detail={b.detail} />
                      <div className="muted" style={{ marginTop: 6 }}>
                        All prices: {b.prices.map((p) => `${p.bookmaker} ${odds(p.odds)}`).join(' · ')}
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function AllMarkets({ markets }) {
  const [category, setCategory] = useState('all');
  const [query, setQuery] = useState('');
  const [pricedOnly, setPricedOnly] = useState(false);
  const categories = useMemo(() => ['all', ...new Set(markets.map((m) => m.category))], [markets]);
  const rows = markets.filter(
    (m) =>
      (category === 'all' || m.category === category) &&
      (!pricedOnly || m.best_odds != null) &&
      (!query || m.label.toLowerCase().includes(query.toLowerCase())),
  );
  return (
    <div className="card">
      <h3 style={{ marginTop: 0 }}>All markets ({markets.length})</h3>
      <div className="filters">
        <select value={category} onChange={(e) => setCategory(e.target.value)}>
          {categories.map((c) => (
            <option key={c} value={c}>{c === 'all' ? 'All categories' : c}</option>
          ))}
        </select>
        <input placeholder="Filter, e.g. corners or a player" value={query} onChange={(e) => setQuery(e.target.value)} />
        <label className="muted" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <input type="checkbox" checked={pricedOnly} onChange={(e) => setPricedOnly(e.target.checked)} />
          only with odds
        </label>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Selection</th>
              <th>Chance</th>
              <th>Fair</th>
              <th>Best odds</th>
              <th>Edge</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((m, i) => (
              <tr key={i}>
                <td>{m.label}</td>
                <td>{pct(m.probability)}</td>
                <td>{odds(m.fair_odds)}</td>
                <td>
                  {odds(m.best_odds)} {m.bookmaker && <span className="muted">{m.bookmaker}</span>}
                </td>
                <td className={evClass(m.ev)}>{signed(m.ev)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function MatchAnalysis({ match, onBack }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  const run = (refresh) => {
    setLoading(true);
    setError(null);
    const call = refresh ? api.analyseMatch(match.id) : api.matchAnalysis(match.id);
    call
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => run(true), [match.id]);

  const ctx = data?.context || {};
  return (
    <div>
      <div className="card analysis-head">
        <div>
          <button className="link" onClick={onBack}>← fixtures</button>
          <h2 style={{ margin: '6px 0 2px' }}>{match.home_name} vs {match.away_name}</h2>
          <div className="muted">
            {match.date?.slice(0, 16).replace('T', ' ')}
            {data?.match?.referee ? ` · Ref: ${data.match.referee}` : ''}
          </div>
        </div>
        <button className="primary" disabled={loading} onClick={() => run(true)}>
          {loading ? 'Analysing…' : 'Refresh & re-analyse'}
        </button>
      </div>

      {loading && !data && <div className="empty">Pulling latest stats, lineups and odds…</div>}
      {error && <div className="empty">Error: {error}</div>}

      {data && (
        <>
          {data.warnings?.map((w) => (
            <div key={w} className="notice">{w}</div>
          ))}
          <div className="stats-grid card">
            <div>Home last match: <span>{ctx.home_last_match?.slice(0, 10) ?? '—'}</span></div>
            <div>Away last match: <span>{ctx.away_last_match?.slice(0, 10) ?? '—'}</span></div>
            <div>Matches used: <span>{ctx.home_matches_used} / {ctx.away_matches_used}</span></div>
            <div>Lineups: <span>{ctx.lineups_confirmed ? 'confirmed' : 'not yet'}</span></div>
            <div>Bookmakers: <span>{data.bookmakers?.join(', ') || 'none'}</span></div>
            <div>Odds updated: <span>{data.odds_updated_at?.slice(0, 16).replace('T', ' ') ?? '—'}</span></div>
          </div>
          <TopBets bets={data.top_bets || []} />
          <AllMarkets markets={data.markets || []} />
          <p className="muted small-print">
            Chance = model probability (player props assume the player features). Edge = chance × best odds − 1.
            Ranking also weighs the bookmakers&apos; consensus, so a bet the whole market strongly disagrees with is
            ranked lower. Stake is a quarter-Kelly suggestion. Models are estimates, not guarantees — bet responsibly.
          </p>
        </>
      )}
    </div>
  );
}
