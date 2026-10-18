import { ArrowsDownUp, CircleNotch, Path } from '@phosphor-icons/react'
import { useEffect, useId, useState, type FormEvent } from 'react'
import { geocode, type Place, type PlanRequest } from './api'
import { toLocalInput } from './lib'

type Props = {
  loading: boolean
  errors: Record<string, string>
  onSubmit: (req: PlanRequest) => void
}

/** Text input with place suggestions in a native <datalist>. */
function PlaceInput(props: {
  name: string
  label: string
  value: string
  error?: string
  onChange: (text: string, place: Place | null) => void
}) {
  const id = useId()
  const [options, setOptions] = useState<Place[]>([])

  useEffect(() => {
    const q = props.value.trim()
    if (q.length < 3 || options.some((o) => o.label === q)) return
    const ctrl = new AbortController()
    const timer = setTimeout(() => {
      geocode(q, ctrl.signal)
        .then(setOptions)
        .catch(() => {}) // suggestions are optional; submit still geocodes server-side
    }, 250)
    return () => {
      clearTimeout(timer)
      ctrl.abort()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.value])

  return (
    <div className="field">
      <label htmlFor={id}>{props.label}</label>
      <input
        id={id}
        name={props.name}
        list={`${id}-list`}
        autoComplete="off"
        required
        placeholder="City, address or lat,lon"
        value={props.value}
        aria-invalid={!!props.error}
        aria-describedby={props.error ? `${id}-err` : undefined}
        onChange={(e) => {
          const text = e.target.value
          props.onChange(text, options.find((o) => o.label === text) ?? null)
        }}
      />
      <datalist id={`${id}-list`}>
        {options.map((o) => (
          <option key={`${o.label}${o.lat}`} value={o.label} />
        ))}
      </datalist>
      {props.error && (
        <p className="field-error" id={`${id}-err`}>
          {props.error}
        </p>
      )}
    </div>
  )
}

export default function TripForm({ loading, errors, onSubmit }: Props) {
  const [origin, setOrigin] = useState({ text: 'Chicago, Illinois, United States', place: null as Place | null })
  const [dest, setDest] = useState({ text: 'Denver, Colorado, United States', place: null as Place | null })
  const [departure, setDeparture] = useState(() => toLocalInput(new Date(Date.now() + 15 * 60_000)))
  const [load, setLoad] = useState('38000')
  const [interval, setIntervalMi] = useState(25)
  const [hos, setHos] = useState(true)
  const [minDeparture] = useState(() => toLocalInput(new Date()))

  function submit(e: FormEvent) {
    e.preventDefault()
    onSubmit({
      origin: origin.place ?? origin.text,
      destination: dest.place ?? dest.text,
      departure: new Date(departure).toISOString(),
      load_lb: Number(load),
      interval_mi: interval,
      hos,
    })
  }

  return (
    <form className="trip-form" onSubmit={submit} aria-label="Plan a trip">
      <div className="places">
        <PlaceInput
          name="origin"
          label="Origin"
          value={origin.text}
          error={errors.origin}
          onChange={(text, place) => setOrigin({ text, place })}
        />
        <button
          type="button"
          className="swap"
          aria-label="Swap origin and destination"
          onClick={() => {
            setOrigin(dest)
            setDest(origin)
          }}
        >
          <ArrowsDownUp size={16} weight="bold" />
        </button>
        <PlaceInput
          name="destination"
          label="Destination"
          value={dest.text}
          error={errors.destination}
          onChange={(text, place) => setDest({ text, place })}
        />
      </div>

      <div className="row">
        <div className="field">
          <label htmlFor="departure">Departure</label>
          <input
            id="departure"
            name="departure"
            type="datetime-local"
            required
            min={minDeparture}
            value={departure}
            aria-invalid={!!errors.departure}
            onChange={(e) => setDeparture(e.target.value)}
          />
          {errors.departure && <p className="field-error">{errors.departure}</p>}
        </div>
        <div className="field">
          <label htmlFor="load">Load weight (lb)</label>
          <input
            id="load"
            name="load_lb"
            type="number"
            inputMode="numeric"
            required
            min={0}
            max={80000}
            step={500}
            value={load}
            aria-invalid={!!errors.load_lb}
            onChange={(e) => setLoad(e.target.value)}
          />
          {errors.load_lb && <p className="field-error">{errors.load_lb}</p>}
        </div>
      </div>

      <fieldset className="field">
        <legend>Weather sample every</legend>
        <div className="segmented">
          {[10, 25, 50].map((mi) => (
            <label key={mi}>
              <input
                type="radio"
                name="interval"
                value={mi}
                checked={interval === mi}
                onChange={() => setIntervalMi(mi)}
              />
              <span>{mi} mi</span>
            </label>
          ))}
        </div>
      </fieldset>

      <label className="check">
        <input type="checkbox" checked={hos} onChange={(e) => setHos(e.target.checked)} />
        <span>Plan hours-of-service breaks (11 h driving, 30 min break, 10 h rest)</span>
      </label>

      <button type="submit" className="primary" disabled={loading}>
        {loading ? <CircleNotch size={18} className="spin" /> : <Path size={18} weight="bold" />}
        {loading ? 'Checking routes' : 'Find safest route'}
      </button>
    </form>
  )
}
