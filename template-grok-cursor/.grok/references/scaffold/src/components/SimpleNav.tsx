import { MemberSignInButton } from './MemberSignInButton'
import { SITE_NAME } from '../lib/site'

export function SimpleNav() {
  return (
    <nav className="fixed top-0 left-0 right-0 z-50 border-b border-white/10 bg-[#0a0a0f]/90 backdrop-blur-md">
      <div className="max-w-4xl mx-auto px-5 h-14 flex items-center justify-between">
        <span className="font-medium text-white tracking-wide">{SITE_NAME}</span>
        <MemberSignInButton />
      </div>
    </nav>
  )
}