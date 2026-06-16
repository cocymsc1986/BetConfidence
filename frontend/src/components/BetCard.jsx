export default function BetCard({ signal }) {
  const { market, confidence, odds, ev_score, stat_summary, match_context, player_name, team_name } = signal;
  const confPct = confidence != null ? `${Math.round(confidence * 100)}%` : '—';
  const oddsStr = odds != null ? odds.toFixed(2) : '—';
  const evPct = ev_score != null ? `${(ev_score * 100).toFixed(1)}%` : '—';

  const evPill = ev_score == null ? 'pill' : ev_score > 0.05 ? 'pill good' : ev_score > 0 ? 'pill warn' : 'pill bad';
  const confPill = confidence == null ? 'pill' : confidence >= 0.65 ? 'pill good' : confidence >= 0.4 ? 'pill warn' : 'pill bad';

  return (
    <article className="card bet-card">
      <div>
        <h3>{player_name || `${match_context?.home_name ?? ''} vs ${match_context?.away_name ?? ''}`}</h3>
        <div className="market">{market}{team_name ? ` · ${team_name}` : ''}</div>
        <div className="context">
          {match_context?.home_name} vs {match_context?.away_name}
          {match_context?.date ? ` · ${match_context.date.slice(0, 10)}` : ''}
          {match_context?.referee ? ` · Ref: ${match_context.referee}` : ''}
        </div>
      </div>
      <div className="metrics">
        <span className={confPill}>Conf {confPct}</span>
        <span className="pill">Odds {oddsStr}</span>
        <span className={evPill}>EV {evPct}</span>
      </div>
      {stat_summary && (
        <div className="stats-grid">
          {Object.entries(stat_summary).map(([k, v]) => (
            <div key={k}>
              {k.replaceAll('_', ' ')}: <span>{String(v)}</span>
            </div>
          ))}
        </div>
      )}
    </article>
  );
}
