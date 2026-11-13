// One source of truth for "the server is unreachable". `api()` reports network
// failures here, the browser's own online/offline events update it, and the
// banner subscribes to it.
import { useEffect, useState } from "react";

let offline = typeof navigator !== "undefined" && !navigator.onLine;
const listeners = new Set<(value: boolean) => void>();

export const isOffline = () => offline;

export function setOffline(value: boolean) {
  if (offline === value) return;
  offline = value;
  for (const listener of listeners) listener(value);
}

if (typeof window !== "undefined") {
  window.addEventListener("offline", () => setOffline(true));
  window.addEventListener("online", () => setOffline(false));
}

export function useOffline() {
  const [value, setValue] = useState(offline);
  useEffect(() => {
    listeners.add(setValue);
    setValue(offline);
    return () => { listeners.delete(setValue); };
  }, []);
  return value;
}
