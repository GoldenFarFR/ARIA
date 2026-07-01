# Game loop patterns — React + canvas

## Problème : stale closure

```tsx
// ❌ La boucle lit toujours started=false
const [started, setStarted] = useState(false)
useEffect(() => {
  const id = setInterval(() => {
    if (!started) return  // closure figée
    tick()
  }, 100)
  return () => clearInterval(id)
}, [])
```

## Solution : refs pour l'état lu dans la boucle

```tsx
const [ui, setUi] = useState<'ready' | 'playing' | 'over'>('ready')
const uiRef = useRef(ui)
uiRef.current = ui  // sync à chaque render

const gameRef = useRef({ /* positions, score interne */ })

useEffect(() => {
  const id = setInterval(() => {
    if (uiRef.current !== 'playing') return
    // mutate gameRef.current, setUi/setScore pour HUD seulement
  }, TICK_MS)
  return () => clearInterval(id)
}, [])  // deps vides — une seule boucle au mount
```

## Clavier

```tsx
useEffect(() => {
  const onKey = (e: KeyboardEvent) => {
    e.preventDefault()  // si touche consommée
    // lire uiRef.current / phaseRef.current
  }
  window.addEventListener('keydown', onKey, { capture: true })
  return () => window.removeEventListener('keydown', onKey, { capture: true })
}, [resetGame])
```

`capture: true` évite que le focus aille ailleurs (nav, boutons).

## Focus

```tsx
<div ref={shellRef} tabIndex={0} className="game-shell" />
// useEffect mount: shellRef.current?.focus()
// onClick shell: shellRef.current?.focus()
```

## RAF + delta time (Pong)

```tsx
let last = performance.now()
const loop = (now: number) => {
  const dt = Math.min((now - last) / 1000, 0.033)
  last = now
  // position += speed * dt
  requestAnimationFrame(loop)
}
```

## Resize canvas

```tsx
const resize = () => {
  const rect = shell.getBoundingClientRect()
  canvas.width = Math.floor(rect.width)
  canvas.height = Math.floor(rect.height)
}
const ro = new ResizeObserver(resize)
ro.observe(shell)
```

## Mobile

`useTouchControls()` → `matchMedia('(hover: none), (pointer: coarse)')`

Afficher `TouchDirPad` / `TouchVerticalPad` quand true.