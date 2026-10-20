export type Place = { label: string; lat: number; lon: number }

export type Checkpoint = {
  mile: number
  lat: number
  lon: number
  eta: string
  elapsed_s: number
  wind_mph: number
  gust_mph: number
  rain_in_hr: number
  snow_in_hr: number
  level: number
  risk_index: number
  reason: string
  span_start: number
  span_end: number
}

export type Span = { level: number; start_mi: number; end_mi: number; polyline: string }

export type Stop = {
  kind: 'break' | 'rest'
  mile: number
  lat: number
  lon: number
  start: string
  elapsed_s: number
  duration_s: number
}

export type Summary = {
  miles_by_level: { low: number; moderate: number; high: number; severe: number; no_travel: number }
  avg_risk: number
  max_level: number
  duration_s: number
  drive_s: number
}

export type Route = {
  id: string
  name: string
  distance_mi: number
  duration_s: number
  arrival: string
  polyline: string
  spans: Span[]
  checkpoints: Checkpoint[]
  stops: Stop[]
  summary: Summary
  track: [number, number, number][]
  rank: number
  recommended: boolean
}

export type Heatmap = {
  bounds: [[number, number], [number, number]]
  cols: number
  rows: number
  cell_mi: number
  cells: number[]
  times: number[]
  risk: number[][]
  wind: number[][]
  rain: number[][]
  snow: number[][]
}

export type Plan = {
  origin: Place
  destination: Place
  departure: string
  load_lb: number
  interval_mi: 10 | 25 | 50
  hos: boolean
  routes: Route[]
  recommendation: { route_id: string; reason: string; warning: string | null; criteria: string[] }
  heatmap: Heatmap | null // null when the weather API had no budget left for it
  notices: string[]
}

export type PlanRequest = {
  origin: Place | string
  destination: Place | string
  departure: string
  load_lb: number
  interval_mi: number
  hos: boolean
}

/** Field errors from DRF ({field: [messages]}) or a single `detail` message. */
export class ApiError extends Error {
  fields: Record<string, string>

  constructor(message: string, fields: Record<string, string> = {}) {
    super(message)
    this.fields = fields
  }
}

async function parse<T>(res: Response): Promise<T> {
  const data = await res.json().catch(() => ({}))
  if (res.ok) return data as T
  if (res.status === 400 && data && typeof data === 'object' && !('detail' in data)) {
    const fields = Object.fromEntries(
      Object.entries(data as Record<string, unknown>).map(([k, v]) => [k, Array.isArray(v) ? String(v[0]) : String(v)]),
    )
    throw new ApiError('Please fix the highlighted fields.', fields)
  }
  if (res.status === 429) throw new ApiError('Too many requests. Wait a minute and try again.')
  throw new ApiError((data as { detail?: string }).detail ?? `Request failed (${res.status}).`)
}

export async function planTrip(body: PlanRequest, signal?: AbortSignal): Promise<Plan> {
  const res = await fetch('/api/plan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  return parse<Plan>(res)
}

export async function geocode(q: string, signal?: AbortSignal): Promise<Place[]> {
  const res = await fetch(`/api/geocode?q=${encodeURIComponent(q)}`, { signal })
  return parse<Place[]>(res)
}
