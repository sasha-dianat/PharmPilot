/**
 * Global Error Boundary — wraps every dashboard section.
 * Prevents one crashing chart from blanking the entire application.
 * Shows a recoverable error card instead of a blank page.
 */
import { Component, type ReactNode, type ErrorInfo } from 'react'

interface Props {
  children: ReactNode
  label?: string          // e.g. "Command Center" — shown in error card
  inline?: boolean        // smaller inline error for sub-panels
}

interface State {
  hasError: boolean
  message: string
  stack: string
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false, message: '', stack: '' }
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, message: error.message, stack: error.stack || '' }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`[ErrorBoundary: ${this.props.label || 'unknown'}]`, error, info)
  }

  render() {
    if (!this.state.hasError) return this.props.children

    if (this.props.inline) {
      return (
        <div className="flex items-center gap-2 px-3 py-2 bg-red-900/20 border border-red-800
                        rounded-lg text-xs text-red-300">
          <span>⚠</span>
          <span className="truncate">{this.props.label || 'Panel'} error</span>
          <button
            onClick={() => this.setState({ hasError: false, message: '', stack: '' })}
            className="ml-auto underline hover:no-underline flex-shrink-0"
          >
            Retry
          </button>
        </div>
      )
    }

    return (
      <div className="flex items-center justify-center h-full min-h-64 p-8">
        <div className="max-w-lg w-full bg-[#1a1f2e] border border-red-900/50 rounded-xl p-6 space-y-4">
          <div className="flex items-center gap-3">
            <span className="text-3xl">⚠️</span>
            <div>
              <h3 className="font-semibold text-red-300">
                {this.props.label || 'Dashboard'} rendering error
              </h3>
              <p className="text-xs text-slate-500 mt-0.5">
                A component crashed. Your data is safe — click Retry to reload.
              </p>
            </div>
          </div>

          <div className="bg-[#0f1117] rounded-lg p-3 font-mono text-xs text-red-400
                          max-h-32 overflow-y-auto leading-relaxed">
            {this.state.message}
          </div>

          <div className="flex gap-3">
            <button
              onClick={() => this.setState({ hasError: false, message: '', stack: '' })}
              className="px-4 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-500"
            >
              ↺ Retry
            </button>
            <button
              onClick={() => window.location.reload()}
              className="px-4 py-2 bg-slate-700 text-slate-300 text-sm rounded-lg hover:bg-slate-600"
            >
              Reload page
            </button>
          </div>
        </div>
      </div>
    )
  }
}
