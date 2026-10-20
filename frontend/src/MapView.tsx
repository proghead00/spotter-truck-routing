import L, { type LatLngBoundsExpression } from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { memo, useEffect, useMemo } from 'react'
import { CircleMarker, ImageOverlay, MapContainer, Marker, Polyline, TileLayer, Tooltip, useMap } from 'react-leaflet'
import type { Checkpoint, Heatmap, Plan, Route } from './api'
import { useMediaQuery } from './hooks'
import {
  RISK_LABELS,
  decodePolyline,
  formatDuration,
  formatTime,
  heatmapPixels,
  positionAt,
  riskColor,
  type Layer,
} from './lib'

const ROUTE_COLORS: Record<string, string> = { A: '#2563eb', B: '#0891b2', C: '#9333ea' }
const UPSCALE = 8 // heatmap cells are painted then smoothed at 8x before the browser stretches them

const frameCache = new WeakMap<Heatmap, Map<string, string>>()

/** One data URL per (layer, hour), built on first use and kept for instant scrubbing. */
function useHeatmapFrame(heatmap: Heatmap | undefined, layer: Layer, hour: number): string | null {
  return useMemo(() => {
    if (!heatmap) return null
    if (!frameCache.has(heatmap)) frameCache.set(heatmap, new Map())
    const cache = frameCache.get(heatmap)!
    const key = `${layer}:${hour}`
    const hit = cache.get(key)
    if (hit) return hit
    const { cols, rows, cells } = heatmap
    const small = document.createElement('canvas')
    small.width = cols
    small.height = rows
    const big = document.createElement('canvas')
    big.width = cols * UPSCALE
    big.height = rows * UPSCALE
    const sctx = small.getContext('2d')
    const bctx = big.getContext('2d')
    if (!sctx || !bctx) return null // no canvas (tests)
    sctx.putImageData(new ImageData(heatmapPixels(cols, rows, cells, heatmap[layer][hour], layer), cols, rows), 0, 0)
    bctx.imageSmoothingEnabled = true
    bctx.imageSmoothingQuality = 'high'
    bctx.drawImage(small, 0, 0, big.width, big.height)
    const url = big.toDataURL()
    cache.set(key, url)
    return url
  }, [heatmap, layer, hour])
}

function FitToPlan({ plan, reduceMotion }: { plan: Plan; reduceMotion: boolean }) {
  const map = useMap()
  const narrow = useMediaQuery('(max-width: 860px)')
  useEffect(() => {
    const pts = plan.routes.flatMap((r) => r.checkpoints.map((c) => [c.lat, c.lon] as [number, number]))
    map.fitBounds(L.latLngBounds(pts), {
      paddingTopLeft: [40, 40],
      paddingBottomRight: [40, narrow ? 40 : 190], // keep the route clear of the floating timeline
      animate: !reduceMotion,
    })
  }, [plan, map, reduceMotion, narrow])
  return null
}

function CheckpointTooltip({ c, routeId }: { c: Checkpoint; routeId: string }) {
  return (
    <Tooltip direction="top" offset={[0, -6]}>
      <div className="tip">
        <strong>
          Route {routeId} · mile {Math.round(c.mile)}
        </strong>
        <span>ETA {formatTime(c.eta)}</span>
        <span>
          Wind {Math.round(c.wind_mph)} mph (gust {Math.round(c.gust_mph)}) · Rain {c.rain_in_hr.toFixed(2)} in/hr ·
          Snow {c.snow_in_hr.toFixed(1)} in/hr
        </span>
        <span style={{ color: riskColor(c.level), fontWeight: 600 }}>
          {RISK_LABELS[c.level]}: {c.reason}
        </span>
      </div>
    </Tooltip>
  )
}

/** Static route layers. Memoised so scrubbing the timeline never re-renders them. */
const RouteLayers = memo(function RouteLayers({
  routes,
  selectedId,
  onSelect,
}: {
  routes: Route[]
  selectedId: string
  onSelect: (id: string) => void
}) {
  const decoded = useMemo(
    () =>
      Object.fromEntries(
        routes.map((r) => [
          r.id,
          { line: decodePolyline(r.polyline), spans: r.spans.map((s) => ({ ...s, line: decodePolyline(s.polyline) })) },
        ]),
      ),
    [routes],
  )
  const others = routes.filter((r) => r.id !== selectedId)
  const selected = routes.find((r) => r.id === selectedId)

  return (
    <>
      {others.map((r) => {
        const line = decoded[r.id].line
        return (
          <Polyline
            key={`${r.id}-base`}
            positions={line}
            pathOptions={{ color: ROUTE_COLORS[r.id], weight: 5, opacity: 0.55 }}
            eventHandlers={{ click: () => onSelect(r.id) }}
          >
            <Tooltip permanent direction="center" className="route-label" position={line[Math.floor(line.length * 0.45)]}>
              {r.id} · {formatDuration(r.duration_s)}
            </Tooltip>
          </Polyline>
        )
      })}
      {others.flatMap((r) =>
        r.checkpoints.map((c) => (
          <CircleMarker
            key={`${r.id}-${c.mile}`}
            center={[c.lat, c.lon]}
            radius={3}
            pathOptions={{ color: riskColor(c.level), fillColor: riskColor(c.level), fillOpacity: 0.7, weight: 1 }}
          >
            <CheckpointTooltip c={c} routeId={r.id} />
          </CircleMarker>
        )),
      )}
      {selected && (
        <>
          <Polyline
            positions={decoded[selected.id].line}
            pathOptions={{ color: '#0f172a', weight: 10, opacity: 0.55 }}
            interactive={false}
          />
          {decoded[selected.id].spans.map((s, i) => (
            <Polyline
              key={`${selected.id}-span-${i}`}
              positions={s.line}
              pathOptions={{ color: riskColor(s.level), weight: 6, opacity: 1 }}
            />
          ))}
          <Polyline positions={decoded[selected.id].line} pathOptions={{ opacity: 0, weight: 14 }}>
            <Tooltip
              permanent
              direction="center"
              className="route-label selected"
              position={decoded[selected.id].line[Math.floor(decoded[selected.id].line.length * 0.45)]}
            >
              {selected.id} · {formatDuration(selected.duration_s)}
              {selected.recommended ? ' · recommended' : ''}
            </Tooltip>
          </Polyline>
          {selected.checkpoints.map((c) => (
            <CircleMarker
              key={`${selected.id}-${c.mile}`}
              center={[c.lat, c.lon]}
              radius={6}
              pathOptions={{ color: '#ffffff', weight: 2, fillColor: riskColor(c.level), fillOpacity: 1 }}
            >
              <CheckpointTooltip c={c} routeId={selected.id} />
            </CircleMarker>
          ))}
          {selected.stops.map((s) => (
            <Marker
              key={`stop-${s.elapsed_s}`}
              position={[s.lat, s.lon]}
              icon={L.divIcon({ className: `stop-marker ${s.kind}`, html: s.kind === 'rest' ? 'R' : 'B', iconSize: [20, 20] })}
            >
              <Tooltip direction="top" offset={[0, -10]}>
                {s.kind === 'rest' ? '10 h rest' : '30 min break'} at mile {Math.round(s.mile)}, from {formatTime(s.start)}
              </Tooltip>
            </Marker>
          ))}
        </>
      )}
    </>
  )
})

const truckIcon = (id: string, selected: boolean) =>
  L.divIcon({
    className: `truck-marker${selected ? ' selected' : ''}`,
    html: `<span style="background:${ROUTE_COLORS[id]}">${id}</span>`,
    iconSize: selected ? [28, 28] : [22, 22],
  })

function Trucks({ routes, selectedId, elapsed }: { routes: Route[]; selectedId: string; elapsed: number }) {
  const icons = useMemo(
    () => Object.fromEntries(routes.map((r) => [r.id, truckIcon(r.id, r.id === selectedId)])),
    [routes, selectedId],
  )
  return (
    <>
      {routes.map((r) => (
        <Marker
          key={`truck-${r.id}`}
          position={positionAt(r.track, elapsed)}
          icon={icons[r.id]}
          zIndexOffset={r.id === selectedId ? 1000 : 500}
          keyboard={false}
          title={`Route ${r.id} truck`}
        />
      ))}
    </>
  )
}

type Props = {
  plan: Plan | null
  selectedId: string
  onSelect: (id: string) => void
  hour: number
  layer: Layer
  showHeatmap: boolean
}

export default function MapView({ plan, selectedId, onSelect, hour, layer, showHeatmap }: Props) {
  const dark = useMediaQuery('(prefers-color-scheme: dark)')
  const reduceMotion = useMediaQuery('(prefers-reduced-motion: reduce)')
  const frame = useHeatmapFrame(plan?.heatmap ?? undefined, layer, hour)
  const elapsed = plan?.heatmap ? plan.heatmap.times[hour] - Date.parse(plan.departure) / 1000 : 0

  return (
    <MapContainer
      className="map"
      center={[39.5, -96]}
      zoom={4}
      preferCanvas
      zoomAnimation={!reduceMotion}
      fadeAnimation={!reduceMotion}
      markerZoomAnimation={!reduceMotion}
    >
      <TileLayer
        key={dark ? 'dark' : 'light'}
        url={`https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_${dark ? 'Dark' : 'Light'}_Gray_Base/MapServer/tile/{z}/{y}/{x}`}
        attribution='Tiles &copy; Esri, HERE, Garmin, &copy; OpenStreetMap contributors · Routes <a href="https://project-osrm.org/">OSRM</a> · Weather <a href="https://open-meteo.com/">Open-Meteo</a>'
        maxZoom={16}
      />
      {plan && (
        <>
          <FitToPlan plan={plan} reduceMotion={reduceMotion} />
          {showHeatmap && frame && plan.heatmap && (
            <ImageOverlay
              url={frame}
              bounds={plan.heatmap.bounds as LatLngBoundsExpression}
              opacity={0.85}
              className="heatmap-layer"
              interactive={false}
            />
          )}
          <RouteLayers routes={plan.routes} selectedId={selectedId} onSelect={onSelect} />
          <CircleMarker
            center={[plan.origin.lat, plan.origin.lon]}
            radius={8}
            pathOptions={{ color: '#ffffff', weight: 3, fillColor: '#0f172a', fillOpacity: 1 }}
          >
            <Tooltip>Origin: {plan.origin.label}</Tooltip>
          </CircleMarker>
          <CircleMarker
            center={[plan.destination.lat, plan.destination.lon]}
            radius={8}
            pathOptions={{ color: '#0f172a', weight: 3, fillColor: '#ffffff', fillOpacity: 1 }}
          >
            <Tooltip>Destination: {plan.destination.label}</Tooltip>
          </CircleMarker>
          <Trucks routes={plan.routes} selectedId={selectedId} elapsed={elapsed} />
        </>
      )}
    </MapContainer>
  )
}
