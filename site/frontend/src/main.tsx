import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { ApiError, NetworkError } from "./api";
import { setOffline } from "./net";
import "./styles.css";
import "./shadcn.css";

// A tab opened before a deployment may request chunks removed by the new build.
// Retry once per minute so a genuine outage cannot cause an endless reload loop.
window.addEventListener("vite:preloadError", (event) => {
  const key = "tybee:last-chunk-reload";
  try {
    const last = Number(sessionStorage.getItem(key) || 0);
    if (Date.now() - last < 60_000) return;
    sessionStorage.setItem(key, String(Date.now()));
    event.preventDefault();
    window.location.reload();
  } catch { /* ErrorBoundary offers a manual retry when storage is unavailable. */ }
});

// The session expired or was revoked (logout in another tab, employee switched off):
// go to the login page instead of leaving a half-working panel on screen.
const onError = (e: unknown, key?: readonly unknown[]) => {
  if (e instanceof NetworkError) { setOffline(true); return; }
  // The server answered, so the connection itself is fine.
  setOffline(false);
  const path = window.location.pathname;
  // ["me"] is the "am I signed in?" probe itself — App routes to /login on its own.
  if (key?.[0] !== "me" && e instanceof ApiError && e.status === 401 && path !== "/login" && !path.startsWith("/invite/")) {
    window.location.replace("/login");
  }
};

const client = new QueryClient({
  queryCache: new QueryCache({ onError: (e, q) => onError(e, q.queryKey) }),
  mutationCache: new MutationCache({ onError: (e) => onError(e) }),
  defaultOptions: {
    queries: {
      // Retry transient failures (a phone switching networks, a restarting container),
      // but never a 4xx: those will not fix themselves.
      retry: (count, error) => count < 2 && !(error instanceof ApiError && error.status < 500),
      retryDelay: (count) => Math.min(500 * 2 ** count, 3000),
      refetchOnWindowFocus: true,
      // Pushes keep the panel current; a focus refetch is only worth it for
      // data that is genuinely old, not for a tab switch.
      staleTime: 30_000,
    },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={client}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
