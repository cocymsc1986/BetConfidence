import { Fragment, useEffect, useMemo, useState } from 'react';
import { api } from '../api.js';
import { SortTh, useSort } from './Sortable.jsx';

const pct = (p) => (p == null ? '—' : `${(p * 100).toFixed(1)}%`);
const odds = (o) => (o == null ? '—' : o.toFixed(2));
const signed = (v) => (v == null ? '—' : `${v > 0 ? '+' : ''}${(v * 100).toFixed(1)}%`);
const BET_COLUMNS = {
  rank: (b) => b.rank,
  label: (b) => b.label,
  probability: (b) => b.probability,
  market_prob: (b) => b.market_prob,
  fair_odds: (b) => b.fair_odds,
  best_odds: (b) => b.best_odds,
  ev: (b) => b.ev,
  stake: (b) => b.quarter_kelly_stake_pct,
};
const MARKET_COLUMNS = {
  label: (m) => m.label,
  probability: (m) => m.probability,
  fair_odds: (m) => m.fair_odds,
  best_odds: (m) => m.best_odds,
  ev: (m) => m.ev,
};
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
  const ranked = useMemo(() => bets.map((b, i) => ({ ...b, rank: i + 1 })), [bets]);
  const { sorted, sort, toggle } = useSort(ranked, BET_COLUMNS);
  if (!bets.length) return null;
  return (
    <div className="card">
      <h3 style={{ marginTop: 0 }}>Top {bets.length} value bets</h3>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <SortTh col="rank" sort={sort} onSort={toggle}>#</SortTh>
              <SortTh col="label" sort={sort} onSort={toggle}>Bet</SortTh>
              <SortTh col="probability" sort={sort} onSort={toggle} title="Model probability">Chance</SortTh>
              <SortTh col="market_prob" sort={sort} onSort={toggle} title="Bookmakers' de-margined consensus">Market</SortTh>
              <SortTh col="fair_odds" sort={sort} onSort={toggle}>Fair</SortTh>
              <SortTh col="best_odds" sort={sort} onSort={toggle}>Best odds</SortTh>
              <SortTh col="ev" sort={sort} onSort={toggle} title="Model chance × best odds − 1">Edge</SortTh>
              <SortTh col="stake" sort={sort} onSort={toggle} title="Quarter-Kelly stake as % of bankroll">Stake</SortTh>
            </tr>
          </thead>
          <tbody>
            {sorted.map((b) => (
              <Fragment key={b.rank}>
                <tr className="clickable" onClick={() => setOpen(open === b.rank ? null : b.rank)}>
                  <td>{b.rank}</td>
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
                {open === b.rank && (
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

function LikelyValue({ markets }) {
  const rows = useMemo(() => markets.filter((m) => m.probability > 0.5 && m.ev > 0), [markets]);
  const { sorted, sort, toggle } = useSort(rows, BET_COLUMNS, { key: 'ev', dir: 'desc' });
  return (
    <div className="card">
      <h3 style={{ marginTop: 0 }}>Likely &amp; value ({rows.length})</h3>
      <p className="muted" style={{ marginTop: 0 }}>
        Every selection the model gives more than a 50% chance that also has a positive edge at the best price.
      </p>
      {rows.length === 0 ? (
        <div className="muted">No selection is both more likely than not and priced above its fair odds.</div>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <SortTh col="label" sort={sort} onSort={toggle}>Bet</SortTh>
                <SortTh col="probability" sort={sort} onSort={toggle} title="Model probability">Chance</SortTh>
                <SortTh col="market_prob" sort={sort} onSort={toggle} title="Bookmakers' de-margined consensus">Market</SortTh>
                <SortTh col="fair_odds" sort={sort} onSort={toggle}>Fair</SortTh>
                <SortTh col="best_odds" sort={sort} onSort={toggle}>Best odds</SortTh>
                <SortTh col="ev" sort={sort} onSort={toggle} title="Model chance × best odds − 1">Edge</SortTh>
                <SortTh col="stake" sort={sort} onSort={toggle} title="Quarter-Kelly stake as % of bankroll">Stake</SortTh>
              </tr>
            </thead>
            <tbody>
              {sorted.map((b, i) => (
                <tr key={i}>
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
              ))}
            </tbody>
          </table>
        </div>
      )}
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
  const { sorted, sort, toggle } = useSort(rows, MARKET_COLUMNS);
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
              <SortTh col="label" sort={sort} onSort={toggle}>Selection</SortTh>
              <SortTh col="probability" sort={sort} onSort={toggle}>Chance</SortTh>
              <SortTh col="fair_odds" sort={sort} onSort={toggle}>Fair</SortTh>
              <SortTh col="best_odds" sort={sort} onSort={toggle}>Best odds</SortTh>
              <SortTh col="ev" sort={sort} onSort={toggle}>Edge</SortTh>
            </tr>
          </thead>
          <tbody>
            {sorted.map((m, i) => (
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
          <LikelyValue markets={data.markets || []} />
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
