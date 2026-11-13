import { Component, ReactNode } from "react";

// A render error on one page must not blank the whole panel: show it in place, keep the menu working.
export default class ErrorBoundary extends Component<{ children: ReactNode; resetKey: string }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidUpdate(prev: { resetKey: string }) {
    // Navigating elsewhere clears the error without remounting healthy pages.
    if (this.state.error && prev.resetKey !== this.props.resetKey) this.setState({ error: null });
  }

  componentDidCatch(error: Error) {
    console.error(error);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="card" style={{ textAlign: "center", padding: "40px 24px" }}>
        <img src="/cat.png" alt="Кот Тайби" style={{ width: 64, height: 64, margin: "0 auto 14px", display: "block" }} />
        <h2>Ой, страница сломалась</h2>
        <p className="muted" style={{ maxWidth: 520, margin: "0 auto 16px" }}>
          Скорее всего, сайт обновился, а сервер ещё работает на старой версии (или наоборот). Обновите страницу; если не
          поможет — перезапустите панель.
        </p>
        <p className="error">{this.state.error.message}</p>
        <button onClick={() => window.location.reload()} style={{ marginTop: 14 }}>Обновить страницу</button>
      </div>
    );
  }
}
