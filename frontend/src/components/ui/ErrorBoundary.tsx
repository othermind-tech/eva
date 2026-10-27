import { Component, type ReactNode, type ErrorInfo } from 'react'
import { AlertTriangle, RefreshCw } from 'lucide-react'

interface Props {
  children:    ReactNode
  /** Optional label shown in the fallback UI */
  label?:      string
  /** Optional callback when the boundary catches */
  onError?:    (error: Error, info: ErrorInfo) => void
}

interface State {
  error: Error | null
}

/**
 * ErrorBoundary — wraps feature panels and the chat area so a component
 * crash renders a contained fallback instead of blacking out the whole app.
 */
export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    if (import.meta.env.DEV) {
      console.error('[ErrorBoundary] caught:', error, info.componentStack)
    }
    this.props.onError?.(error, info)
  }

  reset = () => this.setState({ error: null })

  render() {
    if (!this.state.error) return this.props.children

    return (
      <div
        className="flex flex-col items-center justify-center gap-3 p-8 h-full text-center"
        style={{ color: 'var(--text-muted)' }}
      >
        <AlertTriangle size={28} style={{ color: 'var(--danger)', opacity: 0.7 }} />
        <div>
          <p className="text-sm font-semibold mb-1" style={{ color: 'var(--text-secondary)' }}>
            {this.props.label ?? 'Something went wrong'}
          </p>
          <p className="text-xs" style={{ color: 'var(--text-muted)', maxWidth: 260 }}>
            {this.state.error.message}
          </p>
        </div>
        <button
          onClick={this.reset}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs transition-all"
          style={{
            background: 'var(--bg-surface2)',
            border:     '1px solid var(--border-mid)',
            color:      'var(--text-secondary)',
          }}
        >
          <RefreshCw size={11} />
          Try again
        </button>
      </div>
    )
  }
}
