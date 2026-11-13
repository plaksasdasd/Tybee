// Support alerts: unread badge in the tab title, a quiet chime and optional
// browser notifications. Everything is opt-out and never fires while the tab
// is visible — the chat itself is feedback enough there.
import { useEffect, useRef, useState } from "react";
import type { SupportSummary } from "./Shift";

const BASE_TITLE = "Тайби — панель";
const PREFS_KEY = "tybee:notify";

export type NotifyPrefs = { sound: boolean; browser: boolean };

const DEFAULTS: NotifyPrefs = { sound: true, browser: false };

export function notifyPrefs(): NotifyPrefs {
  try {
    const raw = JSON.parse(localStorage.getItem(PREFS_KEY) ?? "null");
    if (!raw || typeof raw !== "object") return { ...DEFAULTS };
    return { sound: raw.sound !== false, browser: raw.browser === true };
  } catch {
    return { ...DEFAULTS };
  }
}

export function saveNotifyPrefs(next: NotifyPrefs) {
  try { localStorage.setItem(PREFS_KEY, JSON.stringify(next)); } catch { /* private mode */ }
  window.dispatchEvent(new Event("tybee:notify"));
}

export function useNotifyPrefs(): [NotifyPrefs, (next: NotifyPrefs) => void] {
  const [prefs, setPrefs] = useState(notifyPrefs);
  useEffect(() => {
    const update = () => setPrefs(notifyPrefs());
    window.addEventListener("tybee:notify", update);
    window.addEventListener("storage", update);
    return () => {
      window.removeEventListener("tybee:notify", update);
      window.removeEventListener("storage", update);
    };
  }, []);
  return [prefs, saveNotifyPrefs];
}

export const browserPermission = (): NotificationPermission =>
  typeof Notification === "undefined" ? "denied" : Notification.permission;

/** Browsers only expose notifications (and app install) in a secure context. */
export const notificationsAvailable = () =>
  typeof Notification !== "undefined" && (window.isSecureContext || location.hostname === "localhost");

export async function requestBrowserPermission(): Promise<NotificationPermission> {
  if (!notificationsAvailable()) return "denied";
  if (Notification.permission === "granted") return "granted";
  try { return await Notification.requestPermission(); } catch { return "denied"; }
}

// A browser only lets a page make sound after a real gesture, so the audio
// context is created on the first click or key press and reused afterwards.
let audio: AudioContext | null = null;

export function primeAudio() {
  try {
    const Ctx = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctx) return;
    if (!audio) audio = new Ctx();
    if (audio.state === "suspended") void audio.resume();
  } catch { /* no audio on this device */ }
}

export function playChime() {
  if (!audio || !notifyPrefs().sound) return;
  try {
    // A backgrounded tab may have suspended the context; the tones are scheduled
    // anyway and play as soon as it resumes.
    if (audio.state === "suspended") void audio.resume();
    const start = audio.currentTime;
    // Two short tones a fifth apart: noticeable in an office, not harsh.
    for (const [frequency, delay] of [[880, 0], [1320, 0.13]] as const) {
      const oscillator = audio.createOscillator();
      const gain = audio.createGain();
      oscillator.type = "sine";
      oscillator.frequency.value = frequency;
      gain.gain.setValueAtTime(0.0001, start + delay);
      gain.gain.exponentialRampToValueAtTime(0.05, start + delay + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + delay + 0.16);
      oscillator.connect(gain).connect(audio.destination);
      oscillator.start(start + delay);
      oscillator.stop(start + delay + 0.18);
    }
  } catch { /* audio is a nice-to-have: never let it block the notification */ }
}

let unreadBadge = 0;
let titleOverride: string | null = null;

function applyTitle() {
  const next = titleOverride ?? (unreadBadge > 0 ? `(${unreadBadge}) ${BASE_TITLE}` : BASE_TITLE);
  if (document.title !== next) document.title = next;
}

/** The unread count in the tab title, e.g. "(3) Тайби — панель". */
export function setUnreadBadge(count: number) {
  if (unreadBadge === count) return;
  unreadBadge = count;
  applyTitle();
}

/** A page may need the title for a warning (the shift idle prompt); it wins over the badge. */
export function setTitleOverride(value: string | null) {
  titleOverride = value;
  applyTitle();
}

function notifyNewMessages(title: string, body: string, onOpen: () => void) {
  playChime();
  if (!notifyPrefs().browser || typeof Notification === "undefined") return;
  if (Notification.permission !== "granted" || document.visibilityState === "visible") return;
  try {
    const note = new Notification(title, {
      body, tag: "tybee-support", icon: "/icon-192.png", badge: "/icon-192.png", silent: true,
    });
    note.onclick = () => { window.focus(); onOpen(); note.close(); };
  } catch { /* iOS Safari only allows service-worker notifications */ }
}

/**
 * Watches the unread counter from `/api/support/summary` and reacts to growth.
 * Mount once, in the panel layout.
 */
export function useSupportAlerts(summary: SupportSummary | undefined, enabled: boolean, onOpen: () => void) {
  const previous = useRef<number | null>(null);
  const unread = enabled ? summary?.unread ?? 0 : 0;

  useEffect(() => {
    setUnreadBadge(unread);
    return () => setUnreadBadge(0);
  }, [unread]);

  useEffect(() => {
    if (!enabled) { previous.current = null; return; }
    if (previous.current === null) { previous.current = unread; return; }
    if (unread > previous.current) {
      const added = unread - previous.current;
      notifyNewMessages(
        added === 1 ? "Новое сообщение в саппорте" : `${added} новых сообщения в саппорте`,
        "Клиент ждёт ответа — откройте диалог.",
        onOpen,
      );
    }
    previous.current = unread;
  }, [unread, enabled, onOpen]);
}
