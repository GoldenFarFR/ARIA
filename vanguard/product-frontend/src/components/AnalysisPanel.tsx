import { AlertCircle, HelpCircle, TrendingDown, TrendingUp } from 'lucide-react'
import { ChainBadge } from './ChainBadge'
import { Spinner } from './ui/Spinner'
import {
  SIGNAL_LABELS,
  TIMEFRAME_LABELS,
  formatPrice,
  scoreVerdict,
} from '../lib/chains'
import { TimeframePicker } from './TimeframePicker'
import { trendLabel } from '../lib/timeframes'
import type { PairAnalysis, SignalType, Timeframe } from '../types'

interface AnalysisPanelProps {
  analysis: PairAnalysis | null
  loading: boolean
  error?: string | null
  selectedTimeframes: Timeframe[]
  onTimeframesChange: (timeframes: Timeframe[]) => void
}

function signalStyles(type: SignalType) {
  switch (type) {
    case 'buy':
      return 'text-buy bg-buy/10 border-buy/30'
    case 'sell':
      return 'text-sell bg-sell/10 border-sell/30'
    case 'watch':
      return 'text-watch bg-watch/10 border-watch/30'
    default:
      return 'text-terminal/60 bg-panel-elevated border-border'
  }
}

function SignalIcon({ type }: { type: SignalType }) {
  if (type === 'buy') return <TrendingUp className="w-5 h-5" />
  if (type === 'sell') return <TrendingDown className="w-5 h-5" />
  return <HelpCircle className="w-5 h-5" />
}

export function AnalysisPanel({
  analysis,
  loading,
  error,
  selectedTimeframes,
  onTimeframesChange,
}: AnalysisPanelProps) {
  if (loading) {
    return (
      <div className="pixel-panel p-6 space-y-3">
        <TimeframePicker
          selected={selectedTimeframes}
          onChange={onTimeframesChange}
          disabled
        />
        <div className="text-center py-4">
          <Spinner className="inline-block mb-3" />
          <p className="text-sm text-terminal font-terminal font-medium">Analysis in progress…</p>
          <p className="text-sm text-terminal/50 mt-1 font-terminal">
            {selectedTimeframes.length} chart{selectedTimeframes.length > 1 ? 's' : ''} selected
          </p>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="pixel-panel border-sell/40 bg-sell/5 p-6 flex items-start gap-3">
        <AlertCircle className="w-5 h-5 text-sell shrink-0 mt-0.5" />
        <div>
          <p className="text-sm font-terminal font-medium text-sell">Analysis unavailable</p>
          <p className="text-sm text-sell/80 mt-1 font-terminal">{error}</p>
        </div>
      </div>
    )
  }

  if (!analysis) {
    return null
  }

  const signal = SIGNAL_LABELS[analysis.consensus] ?? SIGNAL_LABELS.neutral
  const verdict = scoreVerdict(analysis.global_score)
  const trend = trendLabel(analysis.trend_index ?? 0)
  const hasData = analysis.timeframes.length > 0
  const trendWidth = Math.max(0, Math.min(100, ((analysis.trend_index ?? 0) + 100) / 2))

  return (
    <div className="pixel-panel overflow-hidden">
      <div className="p-3 border-b-2 border-border bg-panel-elevated">
        <TimeframePicker
          selected={selectedTimeframes}
          onChange={onTimeframesChange}
        />
      </div>

      <div className={`p-5 border-b-2 border-border-bright ${signalStyles(analysis.consensus)}`}>
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <div className="mt-0.5">{signal.emoji}</div>
            <div>
              <p className="pixel-label opacity-70 mb-1">Verdict · your charts</p>
              <h3 className="text-xl font-display flex items-center gap-2">
                <SignalIcon type={analysis.consensus} />
                {signal.label}
              </h3>
              <p className="text-sm opacity-80 mt-1 font-terminal">{signal.hint}</p>
            </div>
          </div>
          <div className="text-right shrink-0 space-y-2">
            <div>
              <div className="text-3xl font-display">{analysis.global_score}</div>
              <div className="text-sm opacity-70 font-terminal">score / 100</div>
              <div className={`text-sm font-terminal font-medium mt-1 ${verdict.color}`}>{verdict.label}</div>
            </div>
            <div className="pt-2 border-t border-border/40">
              <div className={`text-lg font-display ${trend.color}`}>
                {analysis.trend_index > 0 ? '+' : ''}{analysis.trend_index ?? 0}
              </div>
              <div className="text-xs font-terminal opacity-70">trend index</div>
              <div className={`text-xs font-terminal ${trend.color}`}>{trend.label}</div>
            </div>
          </div>
        </div>
        <div className="mt-4 h-3 bg-black/30 overflow-hidden border border-border">
          <div
            className="h-full bg-gradient-to-r from-sell via-watch to-buy transition-all duration-700"
            style={{ width: `${analysis.global_score}%` }}
          />
        </div>
        <div className="mt-2 h-2 bg-black/20 overflow-hidden border border-border/60 relative">
          <div
            className="absolute top-0 h-full w-0.5 bg-terminal/80 transition-all duration-700"
            style={{ left: `${trendWidth}%` }}
          />
        </div>
      </div>

      <div className="px-4 py-3 border-b-2 border-border flex items-center justify-between bg-panel-elevated">
        <div className="flex items-center gap-2">
          <h4 className="pixel-label">Breakdown by timeframe</h4>
          <ChainBadge chainId={analysis.pair.chain_id} />
        </div>
        <span className="text-sm text-terminal/50 font-terminal">
          {analysis.timeframes.length} timeframe{analysis.timeframes.length > 1 ? 's' : ''}
        </span>
      </div>

      <div className="p-4">
        <p className="text-sm text-terminal/60 mb-4 leading-relaxed font-terminal">
          {analysis.summary}
        </p>

        {!hasData ? (
          <div className="pixel-panel-inset border-watch/20 p-4 text-sm text-watch/90 font-terminal">
            This token does not have enough price history on this blockchain.
            Choose a token with more liquidity in the section above.
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {analysis.timeframes.map((tf) => {
              const tfSignal = SIGNAL_LABELS[tf.buy_signal.signal_type] ?? SIGNAL_LABELS.neutral
              return (
                <div
                  key={tf.timeframe}
                  className="pixel-panel-inset p-3"
                >
                  <div className="flex items-center justify-between mb-2">
                    <div>
                      <span className="text-sm font-terminal font-semibold text-terminal">{tf.timeframe}</span>
                      <span className="text-sm text-terminal/50 ml-2 font-terminal">
                        {TIMEFRAME_LABELS[tf.timeframe]}
                      </span>
                    </div>
                    <span
                      className={`text-sm font-terminal font-semibold px-2 py-0.5 border-2 ${signalStyles(tf.buy_signal.signal_type)}`}
                    >
                      {tfSignal.emoji} {tf.buy_signal.score}
                    </span>
                  </div>

                  <div className="grid grid-cols-2 gap-1.5 text-sm mb-2 font-terminal">
                    {tf.indicators.rsi != null && (
                      <div className="pixel-panel-inset px-2 py-1.5">
                        <span className="text-terminal/50">RSI </span>
                        <span className={tf.indicators.rsi < 35 ? 'text-buy' : tf.indicators.rsi > 65 ? 'text-sell' : 'text-terminal'}>
                          {tf.indicators.rsi.toFixed(0)}
                        </span>
                        <div className="text-xs text-terminal/40">
                          {tf.indicators.rsi < 35 ? 'Oversold' : tf.indicators.rsi > 65 ? 'Overbought' : 'Neutral'}
                        </div>
                      </div>
                    )}
                    {tf.indicators.ema_9 != null && tf.indicators.ema_21 != null && (
                      <div className="pixel-panel-inset px-2 py-1.5">
                        <span className="text-terminal/50">Trend </span>
                        <span className={tf.indicators.ema_9 > tf.indicators.ema_21 ? 'text-buy' : 'text-sell'}>
                          {tf.indicators.ema_9 > tf.indicators.ema_21 ? 'Bullish' : 'Bearish'}
                        </span>
                      </div>
                    )}
                  </div>

                  {tf.divergences.length > 0 && (
                    <div className="text-sm text-watch/90 mb-1.5 font-terminal">
                      ⚡ {tf.divergences[0].description}
                    </div>
                  )}

                  {tf.buy_signal.reasons.length > 0 && (
                    <p className="text-sm text-terminal/50 line-clamp-2 font-terminal">
                      {tf.buy_signal.reasons[0]}
                    </p>
                  )}

                  {tf.fibonacci && tf.fibonacci.levels.length > 0 && (
                    <div className="mt-2 text-xs text-terminal/40 font-terminal">
                      Fib {tf.fibonacci.levels[0].label}: {formatPrice(tf.fibonacci.levels[0].price)}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}