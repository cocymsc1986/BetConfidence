import { useEffect, useMemo, useState } from 'react';
import { api } from '../api.js';
import BetCard from '../components/BetCard.jsx';

export default function Signals() {
  const [signals, setSignals] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [marketFilter, setMarketFilter] = useState('all');
  const [minEv, setMinEv] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .signals('today')
      .then((data) => {
        if (!cancelled) setSignals(data.signals || []);
      })
      .catch((e) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  const markets = useMemo(() => {
    const set = new Set(signals.map((s) => s.market));
    return ['all', ...Array.from(set)];
  }, [signals]);

  const filtered = useMemo(() => {
    return signals.filter(
      (s) =>
        (marketFilter === 'all' || s.market === marketFilter) &&
        (s.ev_score ?? -1) >= minEv,
    );
  }, [signals, marketFilter, minEv]);

  const best = useMemo(
    () =>
      signals.filter(
        (s) =>
          (s.confidence ?? 0) >= 0.65 &&
          (s.odds ?? 0) >= 1.8 &&
          (s.ev_score ?? 0) > 0.05,
      ),
    [signals],
  );

  if (loading) return <div className="empty">Loading signals…</div>;
  if (error) return <div className="empty">Error: {error}</div>;

  return (
    <>
      <div className="best-callout">
        <h2>Best Bets · {best.length}</h2>
        <p>Confidence ≥ 65%, odds ≥ 1.80, EV &gt; 5%</p>
      </div>

      {best.slice(0, 5).map((s, i) => (
        <BetCard key={`best-${i}`} signal={s} />
      ))}

      <div className="filters" style={{ marginTop: 24 }}>
        <select value={marketFilter} onChange={(e) => setMarketFilter(e.target.value)}>
          {markets.map((m) => (
            <option key={m} value={m}>
              {m === 'all' ? 'All markets' : m}
            </option>
          ))}
        </select>
        <input
          type="number"
          step="0.01"
          value={minEv}
          onChange={(e) => setMinEv(parseFloat(e.target.value) || 0)}
          placeholder="Min EV"
        />
      </div>

      {filtered.length === 0 ? (
        <div className="empty">No signals match your filters.</div>
      ) : (
        filtered.map((s, i) => <BetCard key={i} signal={s} />)
      )}
    </>
  );
}
