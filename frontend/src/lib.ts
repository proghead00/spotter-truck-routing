/** Pure helpers: polyline decoding, risk colours, heatmap pixels, truck position, formatting. */

export const RISK_LABELS = ['Low', 'Moderate', 'High', 'Severe', 'No Travel'] as const
export const RISK_KEYS = ['low', 'moderate', 'high', 'severe', 'no_travel'] as const

/** Risk colours, ordered by level. Mirrored as CSS variables for the UI chrome. */
export const RISK_RGB: [number, number, number][] = [
  [34, 160, 107], // low
  [226, 178, 18], // moderate
  [238, 117, 26], // high
  [214, 40, 57], // severe
  [124, 58, 237], // no travel
]

export const riskColor = (level: number) => `rgb(${RISK_RGB[Math.max(0, Math.min(4, level))].join(',')})`

export function decodePolyline(encoded: string, precision = 5): [number, number][] {
  const coords: [number, number][] = []
  const factor = 10 ** precision
  let index = 0
  let lat = 0
  let lon = 0
  while (index < encoded.length) {
    for (const axis of [0, 1]) {
      let shift = 0
      let result = 0
      let byte: number
      do {
        byte = encoded.charCodeAt(index++) - 63
        result |= (byte & 0x1f) << shift
        shift += 5
      } while (byte >= 0x20)
      const delta = result & 1 ? ~(result >> 1) : result >> 1
      if (axis === 0) lat += delta
      else lon += delta
    }
    coords.push([lat / factor, lon / factor])
  }
  return coords
}

// --------------------------------------------------------------------------- heatmap

export type Layer = 'risk' | 'wind' | 'rain' | 'snow'

type Stop = [number, [number, number, number], number] // value, rgb, alpha

/** Colour ramps. Risk uses the level colours; the raw layers share the risk thresholds
 *  as anchor points so "orange" means High no matter which layer is shown. */
export const RAMPS: Record<Layer, { stops: Stop[]; unit: string; ticks: number[] }> = {
  risk: {
    unit: '',
    ticks: [0, 1, 2, 3, 4],
    stops: [
      [0, RISK_RGB[0], 0.18],
      [1, RISK_RGB[1], 0.5],
      [2, RISK_RGB[2], 0.62],
      [3, RISK_RGB[3], 0.72],
      [4, RISK_RGB[4], 0.8],
    ],
  },
  wind: {
    unit: 'mph',
    ticks: [0, 25, 35, 45, 55],
    stops: [
      [0, [56, 132, 196], 0.05],
      [15, [56, 132, 196], 0.25],
      [25, RISK_RGB[1], 0.5],
      [35, RISK_RGB[2], 0.62],
      [45, RISK_RGB[3], 0.72],
      [55, RISK_RGB[4], 0.8],
    ],
  },
  rain: {
    unit: 'in/hr',
    ticks: [0, 0.1, 0.25, 0.5, 1],
    stops: [
      [0, [56, 132, 196], 0],
      [0.02, [56, 132, 196], 0.25],
      [0.1, RISK_RGB[1], 0.5],
      [0.25, RISK_RGB[2], 0.62],
      [0.5, RISK_RGB[3], 0.72],
      [1, RISK_RGB[4], 0.8],
    ],
  },
  snow: {
    unit: 'in/hr',
    ticks: [0, 0.5, 1, 2, 3],
    stops: [
      [0, [120, 160, 220], 0],
      [0.1, [120, 160, 220], 0.3],
      [0.5, RISK_RGB[1], 0.5],
      [1, RISK_RGB[2], 0.62],
      [2, RISK_RGB[3], 0.72],
      [3, RISK_RGB[4], 0.8],
    ],
  },
}

export function rampColor(layer: Layer, value: number): [number, number, number, number] {
  const stops = RAMPS[layer].stops
  if (value <= stops[0][0]) return [...stops[0][1], stops[0][2]]
  for (let i = 1; i < stops.length; i++) {
    const [v1, c1, a1] = stops[i]
    if (value <= v1) {
      const [v0, c0, a0] = stops[i - 1]
      const t = (value - v0) / (v1 - v0)
      const mix = (a: number, b: number) => a + (b - a) * t
      return [mix(c0[0], c1[0]), mix(c0[1], c1[1]), mix(c0[2], c1[2]), mix(a0, a1)]
    }
  }
  const last = stops[stops.length - 1]
  return [...last[1], last[2]]
}

/** RGBA pixels (row-major, north first) for one heatmap frame; cells outside the corridor stay transparent. */
export function heatmapPixels(
  cols: number,
  rows: number,
  cells: number[],
  values: number[],
  layer: Layer,
): Uint8ClampedArray<ArrayBuffer> {
  const px = new Uint8ClampedArray(cols * rows * 4)
  cells.forEach((cell, i) => {
    const [r, g, b, a] = rampColor(layer, values[i])
    px.set([r, g, b, Math.round(a * 255)], cell * 4)
  })
  return px
}

// --------------------------------------------------------------------------- truck position

/** Where the truck is `t` seconds after departure: interpolated on the [lat, lon, t] track, clamped to the ends. */
export function positionAt(track: [number, number, number][], t: number): [number, number] {
  if (t <= track[0][2]) return [track[0][0], track[0][1]]
  const last = track[track.length - 1]
  if (t >= last[2]) return [last[0], last[1]]
  let lo = 0
  let hi = track.length - 1
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1
    if (track[mid][2] <= t) lo = mid
    else hi = mid
  }
  const [lat0, lon0, t0] = track[lo]
  const [lat1, lon1, t1] = track[hi]
  const f = t1 === t0 ? 0 : (t - t0) / (t1 - t0)
  return [lat0 + (lat1 - lat0) * f, lon0 + (lon1 - lon0) * f]
}

// --------------------------------------------------------------------------- formatting

export function formatDuration(seconds: number): string {
  const minutes = Math.round(seconds / 60)
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return h ? `${h}h ${String(m).padStart(2, '0')}m` : `${m}m`
}

const timeFmt = new Intl.DateTimeFormat(undefined, { weekday: 'short', hour: 'numeric', minute: '2-digit' })
export const formatTime = (iso: string | number) => timeFmt.format(new Date(iso))

export const formatMiles = (mi: number) => `${Math.round(mi).toLocaleString()} mi`

/** "2026-10-18T14:30" in the browser's zone, for <input type="datetime-local">. */
export function toLocalInput(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}
