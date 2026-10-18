import { Truck } from '@phosphor-icons/react'
import { lazy, Suspense, useCallback, useRef, useState } from 'react'
import { ApiError, planTrip, type Plan, type PlanRequest } from './api'
import type { Layer } from './lib'
import RouteList from './RouteList'
import Timeline from './Timeline'
import TripForm from './TripForm'

const MapView = lazy(() => import('./MapView'))

function Skeleton() {
  return (
    <div className="skeleton" aria-hidden>
      <div className="sk-line wide" />
      {[0, 1, 2].map((i) => (
        <div key={i} className="sk-card" />
      ))}
    </div>
  )
}

export default function App() {
  const [plan, setPlan] = useState<Plan | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})
  const [selectedId, setSelectedId] = useState('A')
  const [hour, setHour] = useState(0)
  const [layer, setLayer] = useState<Layer>('risk')
  const [showHeatmap, setShowHeatmap] = useState(true)
  const inflight = useRef<AbortController | null>(null)

  const submit = useCallback(async (req: PlanRequest) => {
    inflight.current?.abort()
    const ctrl = new AbortController()
    inflight.current = ctrl
    setLoading(true)
    setError(null)
    setFieldErrors({})
    try {
      const result = await planTrip(req, ctrl.signal)
      setPlan(result)
      setSelectedId(result.recommendation.route_id)
      setHour(0)
    } catch (e) {
      if (ctrl.signal.aborted) return
      if (e instanceof ApiError) {
        setError(e.message)
        setFieldErrors(e.fields)
      } else {
        setError('Could not reach the server. Check your connection and try again.')
      }
    } finally {
      if (inflight.current === ctrl) setLoading(false)
    }
  }, [])

  return (
    <div className="app">
      <aside className="panel">
        <header className="brand">
          <Truck size={22} weight="duotone" />
          <div>
            <h1>Weather-aware truck routing</h1>
            <p>Three routes, weather at each checkpoint when the truck gets there, and the safest pick.</p>
          </div>
        </header>
        <TripForm loading={loading} errors={fieldErrors} onSubmit={submit} />
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        {loading && !plan && <Skeleton />}
        {plan && (
          <div className={loading ? 'stale' : ''}>
            <RouteList plan={plan} selectedId={selectedId} onSelect={setSelectedId} />
          </div>
        )}
        {!plan && !loading && (
          <p className="empty">
            Enter a trip to compare three routes. Each route is checked against the forecast for the hour the truck
            reaches every checkpoint.
          </p>
        )}
      </aside>
      <main className="map-wrap">
        <Suspense fallback={<div className="map map-loading" />}>
          <MapView
            plan={plan}
            selectedId={selectedId}
            onSelect={setSelectedId}
            hour={hour}
            layer={layer}
            showHeatmap={showHeatmap}
          />
        </Suspense>
        {plan && (
          <Timeline
            heatmap={plan.heatmap}
            hour={hour}
            onHour={setHour}
            layer={layer}
            onLayer={setLayer}
            showHeatmap={showHeatmap}
            onShowHeatmap={setShowHeatmap}
          />
        )}
      </main>
    </div>
  )
}
