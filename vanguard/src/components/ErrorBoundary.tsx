import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('Vanguard render error:', error, info.componentStack)
  }

  render() {
    if (this.state.error) {
      return (
        <div className="min-h-screen vanguard-mesh flex items-center justify-center p-6">
          <div className="max-w-md glass-vanguard rounded-sm p-8 border border-[#c9a962]/20 text-center space-y-4">
            <p className="font-display text-lg text-[#f4efe6]">Erreur d&apos;affichage</p>
            <p className="text-sm text-[#9a958a] font-light leading-relaxed">
              Recharge la page (Ctrl+F5). Si le problème continue, déconnecte-toi de Privy puis reconnecte-toi.
            </p>
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="btn-vanguard-secondary px-6 py-3 text-sm tracking-wide focus-ring"
            >
              Recharger
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}