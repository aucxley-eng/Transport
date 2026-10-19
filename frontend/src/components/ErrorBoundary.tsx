import { Component, type ErrorInfo, type ReactNode } from "react";

/**
 * Catches a render error anywhere below it and shows a recoverable screen
 * rather than a blank white page. React has no hook form for this; an error
 * boundary has to be a class.
 */

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // The console is the only sink there is until a reporting service exists.
    console.error("Unhandled UI error", error, info.componentStack);
  }

  render() {
    if (this.state.error !== null) {
      return (
        <div className="centred">
          <h1>Something went wrong</h1>
          <p role="alert">{this.state.error.message}</p>
          <button
            type="button"
            className="primary"
            onClick={() => window.location.reload()}
          >
            Reload
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
