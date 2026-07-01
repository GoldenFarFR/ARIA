import { TIMEFRAME_LABELS } from '../lib/chains'
import { cn } from '../lib/cn'
import {
  ALL_TIMEFRAMES,
  MAX_TIMEFRAMES,
  MIN_TIMEFRAMES,
} from '../lib/timeframes'
import type { Timeframe } from '../types'

interface TimeframePickerProps {
  selected: Timeframe[]
  onChange: (next: Timeframe[]) => void
  disabled?: boolean
}

export function TimeframePicker({ selected, onChange, disabled }: TimeframePickerProps) {
  const atMax = selected.length >= MAX_TIMEFRAMES
  const atMin = selected.length <= MIN_TIMEFRAMES

  const toggle = (tf: Timeframe) => {
    if (disabled) return
    const isOn = selected.includes(tf)
    if (isOn) {
      if (atMin) return
      onChange(selected.filter((t) => t !== tf))
      return
    }
    if (atMax) return
    onChange(
      [...selected, tf].sort(
        (a, b) => ALL_TIMEFRAMES.indexOf(a) - ALL_TIMEFRAMES.indexOf(b),
      ),
    )
  }

  return (
    <div className="pixel-panel-inset p-3">
      <div className="flex items-center justify-between gap-2 mb-2">
        <p className="pixel-label">Your charts ({selected.length}/{MAX_TIMEFRAMES})</p>
        <p className="text-xs text-terminal/40 font-terminal">
          Pick {MIN_TIMEFRAMES}–{MAX_TIMEFRAMES} timeframes
        </p>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {ALL_TIMEFRAMES.map((tf) => {
          const isOn = selected.includes(tf)
          const lockedOff = !isOn && atMax
          const lockedOn = isOn && atMin
          return (
            <button
              key={tf}
              type="button"
              disabled={disabled || lockedOff || lockedOn}
              onClick={() => toggle(tf)}
              title={TIMEFRAME_LABELS[tf]}
              className={cn(
                'px-2.5 py-1 text-sm font-terminal transition-colors',
                isOn ? 'pixel-btn pixel-btn-active' : 'pixel-btn text-terminal/50',
                (lockedOff || lockedOn || disabled) && 'opacity-50 cursor-not-allowed',
              )}
            >
              {tf}
            </button>
          )
        })}
      </div>
    </div>
  )
}