import { describe, expect, it } from 'vitest'
import {
  RISK_RGB,
  decodePolyline,
  formatDuration,
  heatmapPixels,
  positionAt,
  rampColor,
  riskColor,
  toLocalInput,
} from './lib'

describe('decodePolyline', () => {
  it('decodes the reference example', () => {
    const pts = decodePolyline('_p~iF~ps|U_ulLnnqC_mqNvxq`@')
    expect(pts).toHaveLength(3)
    expect(pts[0][0]).toBeCloseTo(38.5)
    expect(pts[0][1]).toBeCloseTo(-120.2)
    expect(pts[2][0]).toBeCloseTo(43.252)
    expect(pts[2][1]).toBeCloseTo(-126.453)
  })

  it('returns nothing for an empty string', () => {
    expect(decodePolyline('')).toEqual([])
  })
})

describe('colours', () => {
  it('maps risk levels and clamps out-of-range values', () => {
    expect(riskColor(3)).toBe(`rgb(${RISK_RGB[3].join(',')})`)
    expect(riskColor(9)).toBe(riskColor(4))
    expect(riskColor(-1)).toBe(riskColor(0))
  })

  it('puts the raw-layer thresholds on the same colours as the risk levels', () => {
    expect(rampColor('wind', 45).slice(0, 3)).toEqual(RISK_RGB[3]) // 45 mph starts Severe
    expect(rampColor('rain', 0.25).slice(0, 3)).toEqual(RISK_RGB[2]) // 0.25 in/hr starts High
    expect(rampColor('snow', 3).slice(0, 3)).toEqual(RISK_RGB[4])
    expect(rampColor('risk', 99)).toEqual([...RISK_RGB[4], 0.8])
  })

  it('interpolates between stops', () => {
    const [r] = rampColor('risk', 0.5)
    expect(r).toBeCloseTo((RISK_RGB[0][0] + RISK_RGB[1][0]) / 2)
  })
})

describe('heatmapPixels', () => {
  it('paints only corridor cells, row-major', () => {
    const px = heatmapPixels(3, 2, [1, 5], [4, 0], 'risk')
    expect(px).toHaveLength(3 * 2 * 4)
    expect(Array.from(px.slice(4, 8))).toEqual([...RISK_RGB[4], Math.round(0.8 * 255)])
    expect(Array.from(px.slice(20, 24))).toEqual([...RISK_RGB[0], Math.round(0.18 * 255)])
    expect(px[3]).toBe(0) // cell 0 untouched -> transparent
  })
})

describe('positionAt', () => {
  const track: [number, number, number][] = [
    [0, 0, 0],
    [0, 1, 100],
    [0, 1, 200], // parked
    [0, 2, 300],
  ]

  it('interpolates, holds still during stops and clamps', () => {
    expect(positionAt(track, 50)).toEqual([0, 0.5])
    expect(positionAt(track, 150)).toEqual([0, 1])
    expect(positionAt(track, 250)).toEqual([0, 1.5])
    expect(positionAt(track, -10)).toEqual([0, 0])
    expect(positionAt(track, 999)).toEqual([0, 2])
  })
})

describe('formatting', () => {
  it('formats durations', () => {
    expect(formatDuration(59 * 60)).toBe('59m')
    expect(formatDuration(28 * 3600 + 18 * 60)).toBe('28h 18m')
    expect(formatDuration(3600 + 5 * 60)).toBe('1h 05m')
  })

  it('formats datetime-local values in local time', () => {
    expect(toLocalInput(new Date(2026, 0, 5, 7, 3))).toBe('2026-01-05T07:03')
  })
})
