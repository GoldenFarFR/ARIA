import { SimpleNav } from '../components/SimpleNav'
import { SITE_NAME, SITE_TAGLINE } from '../lib/site'

export function HomePage() {
  return (
    <div className="site-mesh min-h-screen">
      <SimpleNav />
      <main className="flex flex-col items-center justify-center min-h-screen px-5 text-center">
        <h1 className="text-4xl md:text-5xl font-semibold text-white mb-4 tracking-tight">
          {SITE_NAME}
        </h1>
        <p className="text-lg text-neutral-400 max-w-md leading-relaxed">{SITE_TAGLINE}</p>
      </main>
      <footer className="pb-8 text-center text-xs text-neutral-600">
        {SITE_NAME} · member access via Privy
      </footer>
    </div>
  )
}