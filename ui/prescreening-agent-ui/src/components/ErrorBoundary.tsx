import { Component, type ErrorInfo, type ReactNode } from 'react';

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
}

// Catches render-time errors in a major UI section so a single failure doesn't blank the app.
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    // Replace with a real logging/monitoring integration when one is introduced.
    console.error('Unhandled UI error', error, info);
  }

  override render(): ReactNode {
    if (this.state.hasError) {
      return (
        <div role="alert" style={{ padding: '1.5rem', textAlign: 'center' }}>
          <h1>Something went wrong</h1>
          <p>Please close and reopen the app. If the problem continues, contact support.</p>
        </div>
      );
    }

    return this.props.children;
  }
}
