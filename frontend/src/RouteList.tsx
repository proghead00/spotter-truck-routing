import { Bed, Coffee, Warning } from '@phosphor-icons/react'
import type { Plan, Route } from './api'
import { RISK_KEYS, RISK_LABELS, formatDuration, formatMiles, formatTime, riskColor } from './lib'

/** Proportional bar of the route's miles, coloured by each checkpoint's risk. */
function RiskStrip({ route }: { route: Route }) {
  return (
    <div className="strip" role="img" aria-label={stripLabel(route)}>
      {route.spans.map((s, i) => (
        <span
          key={i}
          style={{ flexGrow: s.end_mi - s.start_mi, background: riskColor(s.level) }}
          title={`${RISK_LABELS[s.level]}: mile ${Math.round(s.start_mi)}-${Math.round(s.end_mi)}`}
        />
      ))}
    </div>
  )
}

function stripLabel(route: Route) {
  const parts = RISK_KEYS.map((k, i) => [RISK_LABELS[i], route.summary.miles_by_level[k]] as const)
    .filter(([, mi]) => mi > 0)
    .map(([label, mi]) => `${label} ${Math.round(mi)} mi`)
  return `Risk along route: ${parts.join(', ')}`
}

function RouteCard({ route, selected, onSelect }: { route: Route; selected: boolean; onSelect: () => void }) {
  const m = route.summary.miles_by_level
  const breaks = route.stops.filter((s) => s.kind === 'break').length
  const rests = route.stops.filter((s) => s.kind === 'rest').length
  return (
    <li>
      <button
        type="button"
        className={`route-card${selected ? ' selected' : ''}`}
        aria-pressed={selected}
        onClick={onSelect}
        data-route={route.id}
      >
        <div className="route-head">
          <span className="route-id">{route.id}</span>
          <span className="route-name">{route.name ? `via ${route.name}` : `Route ${route.id}`}</span>
          {route.recommended && <span className="badge">Recommended</span>}
        </div>
        <dl className="route-stats">
          <div>
            <dt>Distance</dt>
            <dd>{formatMiles(route.distance_mi)}</dd>
          </div>
          <div>
            <dt>Travel time</dt>
            <dd>{formatDuration(route.duration_s)}</dd>
          </div>
          <div>
            <dt>Arrive</dt>
            <dd>{formatTime(route.arrival)}</dd>
          </div>
        </dl>
        <RiskStrip route={route} />
        <dl className="risk-miles">
          {(['no_travel', 'severe', 'high', 'moderate'] as const).map((k) => (
            <div key={k} className={m[k] > 0 ? '' : 'zero'}>
              <dt>
                <i style={{ background: riskColor(RISK_KEYS.indexOf(k)) }} />
                {RISK_LABELS[RISK_KEYS.indexOf(k)]}
              </dt>
              <dd>{Math.round(m[k])} mi</dd>
            </div>
          ))}
          <div>
            <dt>Avg risk</dt>
            <dd>{route.summary.avg_risk.toFixed(2)}</dd>
          </div>
        </dl>
        {route.stops.length > 0 && (
          <p className="stops-line">
            <Coffee size={14} /> {breaks} break{breaks === 1 ? '' : 's'}
            {rests > 0 && (
              <>
                <Bed size={14} /> {rests} overnight rest{rests === 1 ? '' : 's'}
              </>
            )}
            <span className="muted">· {formatDuration(route.summary.drive_s)} driving</span>
          </p>
        )}
      </button>
    </li>
  )
}

export function Checkpoints({ route }: { route: Route }) {
  return (
    <details className="checkpoints">
      <summary>
        Route {route.id} checkpoints ({route.checkpoints.length})
      </summary>
      <table>
        <thead>
          <tr>
            <th scope="col">Mile</th>
            <th scope="col">ETA</th>
            <th scope="col">Wind</th>
            <th scope="col">Rain</th>
            <th scope="col">Snow</th>
            <th scope="col">Risk</th>
          </tr>
        </thead>
        <tbody>
          {route.checkpoints.map((c) => (
            <tr key={c.mile}>
              <td>{Math.round(c.mile)}</td>
              <td>{formatTime(c.eta)}</td>
              <td>{Math.round(c.wind_mph)}</td>
              <td>{c.rain_in_hr.toFixed(2)}</td>
              <td>{c.snow_in_hr.toFixed(1)}</td>
              <td>
                <span className="level" style={{ color: riskColor(c.level) }}>
                  {RISK_LABELS[c.level]}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  )
}

export default function RouteList({
  plan,
  selectedId,
  onSelect,
}: {
  plan: Plan
  selectedId: string
  onSelect: (id: string) => void
}) {
  const ordered = [...plan.routes].sort((a, b) => a.rank - b.rank)
  const selected = plan.routes.find((r) => r.id === selectedId) ?? ordered[0]
  return (
    <section className="results" aria-label="Route options">
      <div className="recommendation" role="status">
        <p>
          <strong>Route {plan.recommendation.route_id}</strong> is the safest choice. {plan.recommendation.reason}
        </p>
        {plan.recommendation.warning && (
          <p className="warning">
            <Warning size={16} weight="fill" /> {plan.recommendation.warning}
          </p>
        )}
      </div>
      <ul className="route-list">
        {ordered.map((r) => (
          <RouteCard key={r.id} route={r} selected={r.id === selected.id} onSelect={() => onSelect(r.id)} />
        ))}
      </ul>
      <Checkpoints route={selected} />
      <p className="method">
        Ranked by fewest No Travel miles, then Severe, then High, then lowest average risk, then shortest time. Each
        checkpoint covers the road halfway to its neighbours.
      </p>
    </section>
  )
}
