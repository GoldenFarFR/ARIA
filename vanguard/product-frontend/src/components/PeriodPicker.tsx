import { cn } from '../lib/cn'
import {
  ALL_STATS_PERIODS,
  MAX_PERIODS,
  MIN_PERIODS,
  PERIOD_LABELS,
  type StatsPeriod,
} from '../lib/periods'

interface PeriodPickerProps {
  selected: StatsPeriod[]
  onChange: (next: StatsPeriod[]) => void
  disabled?: boolean
}

export function PeriodPicker({ selected, onChange, disabled }: PeriodPickerProps) {
  const atMax = selected.length >= MAX_PERIODS
  const atMin = selected.length <= MIN_PERIODS

  const toggle = (period: StatsPeriod) => {
    if (disabled) return
    const isOn = selected.includes(period)
    if (isOn) {
      if (atMin) return
      onChange(selected.filter((p) => p !== period))
      return
    }
    if (atMax) return
    onChange(
      [...selected, period].sort(
        (a, b) => ALL_STATS_PERIODS.indexOf(a) - ALL_STATS_PERIODS.indexOf(b),
      ),
    )
  }

  return (
    <div className="pixel-panel-inset p-3">
      <div className="flex items-center justify-between gap-2 mb-2">
        <p className="pixel-label">
          Period breakdown ({selected.length}/{MAX_PERIODS})
        </p>
        <p className="text-xs text-terminal/40 font-terminal">
          Pick {MIN_PERIODS}–{MAX_PERIODS} periods
        </p>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {ALL_STATS_PERIODS.map((period) => {
          const isOn = selected.includes(period)
          const lockedOff = !isOn && atMax
          const lockedOn = isOn && atMin
          return (
            <button
              key={period}
              type="button"
              disabled={disabled || lockedOff || lockedOn}
              onClick={() => toggle(period)}
              title={PERIOD_LABELS[period]}
              className={cn(
                'px-2.5 py-1 text-sm font-terminal transition-colors uppercase',
                isOn ? 'pixel-btn pixel-btn-active' : 'pixel-btn text-terminal/50',
                (lockedOff || lockedOn || disabled) && 'opacity-50 cursor-not-allowed',
              )}
            >
              {period}
            </button>
          )
        })}
      </div>
    </div>
  )
}