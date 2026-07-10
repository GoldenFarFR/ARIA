export interface GaugeZone {
  from: number
  to: number
  color: string
}

interface LinearGaugeProps {
  value: number
  min: number
  max: number
  zones: GaugeZone[]
  height?: number
}

/** Jauge linéaire fine : piste zonée (survente/neutre/surachat, etc.) + marqueur
 * de position. Une seule échelle continue, jamais un dégradé arc-en-ciel — les
 * zones viennent du même vocabulaire sémantique que le reste du produit. */
export function LinearGauge({ value, min, max, zones, height = 6 }: LinearGaugeProps) {
  const span = max - min
  const clamped = Math.max(min, Math.min(max, value))
  const pct = span > 0 ? ((clamped - min) / span) * 100 : 50

  return (
    <div className="relative w-full" style={{ height: height + 12, marginTop: 4 }}>
      <div
        className="absolute inset-x-0 rounded-full overflow-hidden flex"
        style={{ height, top: 6 }}
      >
        {zones.map((z, i) => {
          const from = Math.max(min, z.from)
          const to = Math.min(max, z.to)
          const widthPct = span > 0 ? Math.max(0, ((to - from) / span) * 100) : 0
          return (
            <div
              key={i}
              style={{ width: `${widthPct}%`, backgroundColor: z.color, opacity: 0.3 }}
            />
          )
        })}
      </div>
      <div
        className="absolute rounded-full"
        style={{
          left: `${pct}%`,
          top: 0,
          transform: 'translateX(-50%)',
          width: 3,
          height: height + 12,
          backgroundColor: '#f4efe6',
          boxShadow: '0 0 0 3px rgba(38,36,31,0.9)',
        }}
      />
    </div>
  )
}
