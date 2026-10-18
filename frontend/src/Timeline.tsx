import { Pause, Play } from '@phosphor-icons/react'
import { useEffect, useState } from 'react'
import type { Heatmap } from './api'
import { RAMPS, rampColor, type Layer } from './lib'

const LAYERS: { id: Layer; label: string }[] = [
  { id: 'risk', label: 'Risk' },
  { id: 'wind', label: 'Wind' },
  { id: 'rain', label: 'Rain' },
  { id: 'snow', label: 'Snow' },
]
const MAX_HOUR = 48
const PLAY_STEP_MS = 450

const timeFmt = new Intl.DateTimeFormat(undefined, { weekday: 'short', hour: 'numeric', minute: '2-digit' })

function Legend({ layer }: { layer: Layer }) {
  const ramp = RAMPS[layer]
  const lo = ramp.ticks[0]
  const hi = ramp.ticks[ramp.ticks.length - 1]
  const stops = Array.from({ length: 11 }, (_, i) => {
    const [r, g, b, a] = rampColor(layer, lo + ((hi - lo) * i) / 10)
    return `rgba(${r},${g},${b},${Math.max(a, 0.35)}) ${i * 10}%`
  })
  return (
    <div className="legend" aria-label={`${layer} legend`}>
      <div className="legend-bar" style={{ background: `linear-gradient(90deg, ${stops.join(',')})` }} />
      <div className="legend-ticks">
        {layer === 'risk'
          ? ['Low', 'Mod', 'High', 'Severe', 'No go'].map((t) => <span key={t}>{t}</span>)
          : ramp.ticks.map((t) => <span key={t}>{t}</span>)}
      </div>
      {ramp.unit && <span className="legend-unit">{ramp.unit}</span>}
    </div>
  )
}

type Props = {
  heatmap: Heatmap
  hour: number
  onHour: (h: number) => void
  layer: Layer
  onLayer: (l: Layer) => void
  showHeatmap: boolean
  onShowHeatmap: (v: boolean) => void
}

export default function Timeline({ heatmap, hour, onHour, layer, onLayer, showHeatmap, onShowHeatmap }: Props) {
  const [playing, setPlaying] = useState(false)

  useEffect(() => {
    if (!playing) return
    const id = window.setInterval(() => {
      onHour(hour >= MAX_HOUR ? 0 : hour + 1)
    }, PLAY_STEP_MS)
    return () => window.clearInterval(id)
  }, [playing, hour, onHour])

  const at = new Date(heatmap.times[hour] * 1000)
  return (
    <div className="timeline" aria-label="Forecast timeline">
      <div className="timeline-top">
        <button
          type="button"
          className="icon-btn"
          onClick={() => setPlaying((p) => !p)}
          aria-label={playing ? 'Pause forecast' : 'Play forecast'}
        >
          {playing ? <Pause size={16} weight="fill" /> : <Play size={16} weight="fill" />}
        </button>
        <output className="timeline-time" htmlFor="hour">
          <strong>{timeFmt.format(at)}</strong>
          <span>+{hour} h after departure</span>
        </output>
        <div className="segmented small" role="radiogroup" aria-label="Heatmap layer">
          {LAYERS.map((l) => (
            <label key={l.id}>
              <input
                type="radio"
                name="layer"
                checked={layer === l.id}
                onChange={() => {
                  onLayer(l.id)
                  onShowHeatmap(true)
                }}
              />
              <span>{l.label}</span>
            </label>
          ))}
        </div>
        <label className="check small">
          <input type="checkbox" checked={showHeatmap} onChange={(e) => onShowHeatmap(e.target.checked)} />
          <span>Heatmap</span>
        </label>
      </div>
      <input
        id="hour"
        type="range"
        min={0}
        max={MAX_HOUR}
        step={1}
        value={hour}
        aria-label="Forecast hour"
        aria-valuetext={`${timeFmt.format(at)}, ${hour} hours after departure`}
        onChange={(e) => {
          setPlaying(false)
          onHour(Number(e.target.value))
        }}
      />
      <div className="timeline-scale" aria-hidden>
        {[0, 12, 24, 36, 48].map((h) => (
          <span key={h}>{h} h</span>
        ))}
      </div>
      <Legend layer={layer} />
    </div>
  )
}
