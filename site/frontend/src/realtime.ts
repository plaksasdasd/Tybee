import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

/**
 * One WebSocket for the whole panel: the server tells us which area changed and
 * we drop those caches instead of polling. Polling stays as a slow safety net.
 */
const KEYS: Record<string, string[]> = {
  // "support-msgs" is intentionally absent: the open chat appends only new
  // messages (after_id) instead of re-downloading the whole history.
  support: [
    "support-threads", "support-thread", "support-summary", "support-stale",
    "support-handoff", "support-agents", "support-message-search", "my-threads", "shifts", "cabinet",
  ],
  // Reading a thread changes counters only — never the message history.
  "support-unread": ["support-summary", "support-threads"],
  finance: [
    "fin-summary", "fin-ops", "fin-payouts", "fin-rev", "fin-staff", "my-finance",
    "payroll", "payroll-reviews", "leaderboard", "cabinet",
  ],
  staff: ["me", "staff", "roles", "invites", "audit", "audit-actions"],
};

type TopicListener = (topic: string) => void;
const topicListeners = new Set<TopicListener>();

/** Listen for push topics, for caches that need a smarter refresh than invalidation. */
export function subscribeTopic(listener: TopicListener): () => void {
  topicListeners.add(listener);
  return () => { topicListeners.delete(listener); };
}

type State = { connected: boolean; syncedAt: number | null };
let state: State = { connected: false, syncedAt: null };
const listeners = new Set<(value: State) => void>();

const update = (patch: Partial<State>) => {
  state = { ...state, ...patch };
  for (const listener of listeners) listener(state);
};

/** Connection state for status labels and for choosing the fallback poll interval. */
export function useRealtimeState(): State {
  const [value, setValue] = useState(state);
  useEffect(() => {
    listeners.add(setValue);
    setValue(state);
    return () => { listeners.delete(setValue); };
  }, []);
  return value;
}

export function useRealtime(enabled: boolean) {
  const qc = useQueryClient();
  useEffect(() => {
    if (!enabled) return;
    let socket: WebSocket | undefined;
    let timer: number | undefined;
    let attempt = 0;
    let stopped = false;

    const connect = () => {
      if (stopped) return;
      const scheme = location.protocol === "https:" ? "wss:" : "ws:";
      socket = new WebSocket(`${scheme}//${location.host}/api/support/events`);
      socket.onopen = () => {
        attempt = 0;
        update({ connected: true, syncedAt: Date.now() });
        // Anything could have changed while we were away.
        void qc.invalidateQueries();
      };
      socket.onmessage = (event) => {
        update({ syncedAt: Date.now() });
        let topic = "";
        try {
          topic = String(JSON.parse(String(event.data))?.topic ?? "");
        } catch { /* a malformed frame just means "refresh everything below" */ }
        const keys = KEYS[topic];
        if (keys) for (const key of keys) void qc.invalidateQueries({ queryKey: [key] });
        for (const listener of topicListeners) listener(topic);
      };
      socket.onclose = () => {
        update({ connected: false });
        if (stopped) return;
        attempt += 1;
        timer = window.setTimeout(connect, Math.min(30_000, 1000 * 2 ** attempt));
      };
      socket.onerror = () => socket?.close();
    };

    const wake = () => {
      if (stopped || document.visibilityState === "hidden") return;
      if (!socket || socket.readyState > WebSocket.OPEN) {
        if (timer) window.clearTimeout(timer);
        connect();
      }
    };

    connect();
    window.addEventListener("online", wake);
    document.addEventListener("visibilitychange", wake);
    return () => {
      stopped = true;
      if (timer) window.clearTimeout(timer);
      window.removeEventListener("online", wake);
      document.removeEventListener("visibilitychange", wake);
      socket?.close();
      update({ connected: false });
    };
  }, [enabled, qc]);
}
