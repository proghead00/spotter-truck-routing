import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, planTrip, type Plan, type Route } from './api'
import RouteList from './RouteList'
import TripForm from './TripForm'

function route(id: string, overrides: Partial<Route> = {}): Route {
  return {
    id,
    name: 'I 55',
    distance_mi: 300,
    duration_s: 5 * 3600,
    arrival: '2026-10-19T05:00:00Z',
    polyline: '_p~iF~ps|U',
    spans: [{ level: 0, start_mi: 0, end_mi: 300, polyline: '_p~iF~ps|U' }],
    checkpoints: [
      {
        mile: 0,
        lat: 1,
        lon: 1,
        eta: '2026-10-19T00:00:00Z',
        elapsed_s: 0,
        wind_mph: 12,
        gust_mph: 20,
        rain_in_hr: 0,
        snow_in_hr: 0,
        level: 0,
        risk_index: 0.4,
        reason: 'Conditions within normal limits',
        span_start: 0,
        span_end: 300,
      },
    ],
    stops: [],
    summary: {
      miles_by_level: { low: 300, moderate: 0, high: 0, severe: 0, no_travel: 0 },
      avg_risk: 0,
      max_level: 0,
      duration_s: 5 * 3600,
      drive_s: 5 * 3600,
    },
    track: [[1, 1, 0]],
    rank: 1,
    recommended: false,
    ...overrides,
  }
}

const plan = {
  routes: [route('A', { rank: 2 }), route('B', { rank: 1, recommended: true, name: 'US 51' })],
  recommendation: { route_id: 'B', reason: 'Fewest Severe miles: 0 mi vs 25 mi on Route A.', warning: null, criteria: [] },
} as unknown as Plan

describe('RouteList', () => {
  it('lists routes by rank with the recommendation first', () => {
    render(<RouteList plan={plan} selectedId="B" onSelect={() => {}} />)
    const cards = screen.getAllByRole('button', { pressed: true }).concat(screen.getAllByRole('button', { pressed: false }))
    expect(cards[0]).toHaveTextContent('via US 51')
    expect(screen.getByText('Recommended')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Route B is the safest choice. Fewest Severe miles')
  })

  it('selects a route on click', async () => {
    const onSelect = vi.fn()
    render(<RouteList plan={plan} selectedId="B" onSelect={onSelect} />)
    await userEvent.click(screen.getByText('via I 55'))
    expect(onSelect).toHaveBeenCalledWith('A')
  })

  it('shows the unsafe-weather warning', () => {
    const unsafe = { ...plan, recommendation: { ...plan.recommendation, warning: 'Even the recommended route...' } }
    render(<RouteList plan={unsafe} selectedId="B" onSelect={() => {}} />)
    expect(screen.getByText(/Even the recommended route/)).toBeInTheDocument()
  })
})

describe('TripForm', () => {
  afterEach(() => vi.restoreAllMocks())

  it('submits the trip as an API request', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('[]'))
    const onSubmit = vi.fn()
    render(<TripForm loading={false} errors={{}} onSubmit={onSubmit} />)
    const load = screen.getByLabelText('Load weight (lb)')
    await userEvent.clear(load)
    await userEvent.type(load, '45000')
    await userEvent.click(screen.getByLabelText('10 mi'))
    await userEvent.click(screen.getByRole('checkbox', { name: /hours-of-service/ }))
    fireEvent.submit(screen.getByRole('form', { name: 'Plan a trip' }))
    expect(onSubmit).toHaveBeenCalledTimes(1)
    const req = onSubmit.mock.calls[0][0]
    expect(req).toMatchObject({
      origin: 'Chicago, Illinois, United States',
      destination: 'Denver, Colorado, United States',
      load_lb: 45000,
      interval_mi: 10,
      hos: false,
    })
    expect(new Date(req.departure).getTime()).toBeGreaterThan(Date.now())
  })

  it('swaps origin and destination', async () => {
    render(<TripForm loading={false} errors={{}} onSubmit={() => {}} />)
    await userEvent.click(screen.getByRole('button', { name: 'Swap origin and destination' }))
    expect(screen.getByLabelText('Origin')).toHaveValue('Denver, Colorado, United States')
    expect(screen.getByLabelText('Destination')).toHaveValue('Chicago, Illinois, United States')
  })

  it('shows field errors and the loading state', () => {
    render(<TripForm loading errors={{ origin: 'Could not find "Atlantis".' }} onSubmit={() => {}} />)
    expect(screen.getByText('Could not find "Atlantis".')).toBeInTheDocument()
    expect(screen.getByLabelText('Origin')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByRole('button', { name: /Checking routes/ })).toBeDisabled()
  })
})

describe('api', () => {
  afterEach(() => vi.restoreAllMocks())

  it('turns DRF field errors into ApiError.fields', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ load_lb: ['Ensure this value is less than or equal to 80000.'] }), { status: 400 }),
    )
    const err = await planTrip({} as never).catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err.fields.load_lb).toMatch(/80000/)
  })

  it('surfaces upstream failures and rate limits', async () => {
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'Upstream data provider failed' }), { status: 502 }))
      .mockResolvedValueOnce(new Response('{}', { status: 429 }))
    await expect(planTrip({} as never)).rejects.toThrow('Upstream data provider failed')
    await expect(planTrip({} as never)).rejects.toThrow(/Too many requests/)
  })
})
