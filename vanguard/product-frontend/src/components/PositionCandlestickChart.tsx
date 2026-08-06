import { useEffect, useRef } from 'react'
import {
  createChart,
  CandlestickSeries,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from 'lightweight-charts'

export interface DashboardCandle {
  ts: number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface PositionLevels {
  entryPrice: number
  targetPrice: number | null
  invalidationPrice: number | null
  highWaterPrice: number | null
}

interface PositionCandlestickChartProps {
  candles: DashboardCandle[]
  levels: PositionLevels
}

// Real trailing-stop bounds (paper_trader.py, ATR-based) run 1.5%-40% of
// entry depending on mode -- never hardcoded here. When invalidation_price
// is stale (set once at entry, never revised for a trailing stop), the
// high-water-derived level is the one that actually reflects the current
// stop -- shown as a distinct dashed line so the two are never conflated.
export function PositionCandlestickChart({ candles, levels }: PositionCandlestickChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  // Price lines aren't cleared by setData() -- without tracking and removing
  // them ourselves, switching to another position would stack the old
  // entry/TP/SL lines on top of the new chart instead of replacing them.
  const priceLinesRef = useRef<ReturnType<ISeriesApi<'Candlestick'>['createPriceLine']>[]>([])

  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      layout: {
        background: { color: '#141416' },
        textColor: '#c8f0d8',
      },
      grid: {
        vertLines: { color: '#2e2e34' },
        horzLines: { color: '#2e2e34' },
      },
      width: containerRef.current.clientWidth,
      height: 360,
      timeScale: { timeVisible: true, secondsVisible: false },
    })
    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#5bffa8',
      downColor: '#ff6b6b',
      borderVisible: false,
      wickUpColor: '#5bffa8',
      wickDownColor: '#ff6b6b',
    })
    chartRef.current = chart
    seriesRef.current = series

    const onResize = () => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth })
    }
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
    }
  }, [])

  useEffect(() => {
    const series = seriesRef.current
    if (!series) return
    priceLinesRef.current.forEach((line) => series.removePriceLine(line))
    priceLinesRef.current = []
    series.setData(
      candles.map((c) => ({
        time: c.ts as UTCTimestamp,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      })),
    )
    priceLinesRef.current.push(
      series.createPriceLine({
        price: levels.entryPrice,
        color: '#ffd166',
        lineWidth: 1,
        lineStyle: 2,
        title: 'entrée',
      }),
    )
    if (levels.targetPrice != null) {
      priceLinesRef.current.push(
        series.createPriceLine({
          price: levels.targetPrice,
          color: '#5bffa8',
          lineWidth: 1,
          lineStyle: 2,
          title: 'TP',
        }),
      )
    }
    if (levels.invalidationPrice != null) {
      priceLinesRef.current.push(
        series.createPriceLine({
          price: levels.invalidationPrice,
          color: '#ff6b6b',
          lineWidth: 1,
          lineStyle: 2,
          title: 'SL (entrée)',
        }),
      )
    }
    if (levels.highWaterPrice != null && levels.highWaterPrice !== levels.entryPrice) {
      priceLinesRef.current.push(
        series.createPriceLine({
          price: levels.highWaterPrice,
          color: '#a78bfa',
          lineWidth: 1,
          lineStyle: 3,
          title: 'plus haut suivi',
        }),
      )
    }
    chartRef.current?.timeScale().fitContent()
  }, [candles, levels])

  return <div ref={containerRef} className="w-full" />
}
