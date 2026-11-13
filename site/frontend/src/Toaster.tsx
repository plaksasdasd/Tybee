// A single success/error notification style for the whole panel, so actions
// stop confirming themselves in five different ways.
import { useSyncExternalStore } from "react";

export type ToastTone = "ok" | "error" | "info";
export type ToastAction = { label: string; run: () => void };
type ToastItem = { id: number; text: string; tone: ToastTone; action?: ToastAction };

let items: ToastItem[] = [];
let sequence = 0;
const listeners = new Set<() => void>();

const emit = () => { for (const listener of listeners) listener(); };
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
};
const snapshot = () => items;

export function dismissToast(id: number) {
  if (!items.some((item) => item.id === id)) return;
  items = items.filter((item) => item.id !== id);
  emit();
}

export function toast(
  text: string,
  options: { tone?: ToastTone; action?: ToastAction; duration?: number } = {},
) {
  const item: ToastItem = { id: ++sequence, text, tone: options.tone ?? "ok", action: options.action };
  // Keep the stack short: older toasts fall off instead of covering the page.
  items = [...items.slice(-2), item];
  emit();
  window.setTimeout(() => dismissToast(item.id), options.duration ?? (item.tone === "error" ? 7000 : 4000));
}

export const toastError = (text: string, action?: ToastAction) => toast(text, { tone: "error", action });

export function Toaster() {
  const list = useSyncExternalStore(subscribe, snapshot, snapshot);
  return (
    <div className="toasts" role="status" aria-live="polite" aria-atomic="false">
      {list.map((item) => (
        <div key={item.id} className={`toast ${item.tone}`}>
          <span>{item.text}</span>
          {item.action && (
            <button type="button" className="toast-action" onClick={() => { item.action!.run(); dismissToast(item.id); }}>
              {item.action.label}
            </button>
          )}
          <button type="button" className="toast-close" aria-label="Закрыть уведомление" onClick={() => dismissToast(item.id)}>×</button>
        </div>
      ))}
    </div>
  );
}
