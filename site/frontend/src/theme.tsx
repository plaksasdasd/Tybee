import { useEffect, useState } from "react";
import { flushSync } from "react-dom";

export type Theme = "light" | "dark";
/** What the person picked. "system" means: follow the OS until they choose a theme here. */
type Choice = Theme | "system";

const KEY = "tybee:theme";
const EVENT = "tybee:theme";
const QUERY = "(prefers-color-scheme: dark)";
const COLOR: Record<Theme, string> = { light: "#f5f7fb", dark: "#0b0f19" };
/* The wipe comes from the top-right corner, diagonally across the screen. */
const WIPE_FROM = "polygon(100% 0, 100% 0, 100% 0)";
const WIPE_TO = "polygon(100% 0, 100% 200%, -100% 0)";

type ViewTransitionLike = { ready: Promise<void>; finished: Promise<void> };
type DocWithTransitions = Document & { startViewTransition?: (callback: () => void) => ViewTransitionLike };

const query = () => (typeof window !== "undefined" && window.matchMedia ? window.matchMedia(QUERY) : null);
const systemTheme = (): Theme => (query()?.matches ? "dark" : "light");
const choice = (): Choice => {
  try {
    const saved = localStorage.getItem(KEY);
    return saved === "light" || saved === "dark" ? saved : "system";
  } catch {
    return "system";
  }
};
const resolve = (value: Choice): Theme => (value === "system" ? systemTheme() : value);
const read = (): Theme => (document.documentElement.dataset.theme === "dark" ? "dark" : "light");

/**
 * Colours outside the page box — the html canvas behind overscroll, native form
 * controls, scrollbars and the phone status bar — are painted by the browser,
 * not by our CSS, so they have to be updated together with the palette.
 */
export function applyTheme(theme: Theme) {
  const root = document.documentElement;
  if (root.dataset.theme !== theme) root.dataset.theme = theme;
  root.style.colorScheme = theme;
  // iOS also samples the canvas colour of <html> for its bars, so paint it inline
  // instead of relying on the CSS variable alone, and force the layout pass.
  root.style.backgroundColor = COLOR[theme];
  void root.offsetHeight;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta && meta.getAttribute("content") !== COLOR[theme]) meta.setAttribute("content", COLOR[theme]);
  const scheme = document.querySelector('meta[name="color-scheme"]');
  if (scheme) scheme.setAttribute("content", theme);
}

/**
 * Mobile browsers tint the address/status bar and the overscroll gutters from
 * <meta name="theme-color">. iOS Safari and the in-app browsers built on it only
 * re-read the tag when the element itself is inserted — a plain `content` update is
 * ignored until the next navigation, which is exactly why the bars stayed the old
 * colour until a reload. So every change re-creates the tag.
 */
export function announceTheme(theme: Theme) {
  const head = document.head;
  if (!head) return;

  head.querySelectorAll('meta[name="theme-color"]').forEach((node) => node.remove());
  const meta = document.createElement("meta");
  meta.setAttribute("name", "theme-color");
  meta.setAttribute("content", COLOR[theme]);
  head.appendChild(meta);
}

/** Only wide, mouse-driven screens get the diagonal wipe; phones use the normal cross-fade. */
const WIPE_MEDIA = "(min-width: 1001px) and (hover: hover) and (pointer: fine)";

let active = 0;
let seq = 0;

/**
 * Swap the palette behind a view transition. On desktop the new snapshot is revealed
 * diagonally (clip-path on ::view-transition-new(root), as in the EGE panel); on
 * phones and tablets the browser's own cross-fade is used, which is what people
 * expect there.
 *
 * The `theme-switching` class switches every CSS transition off while the snapshots
 * are taken. Without it each button, chip and input fades to the new colours at its
 * own pace, which reads as flicker.
 */
function animateTheme(commit: () => void) {
  const root = document.documentElement;
  const doc = document as DocWithTransitions;
  const calm = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  // Only one transition at a time: a second caller (e.g. two toggles listening to the
  // same system change) must not start a transition on top of the running one.
  if (!doc.startViewTransition || calm || active || document.visibilityState === "hidden") {
    commit();
    return;
  }
  const wipe = window.matchMedia(WIPE_MEDIA).matches;
  const id = ++seq;
  active = id;
  root.classList.add("theme-switching");
  const release = () => {
    if (active !== id) return;
    active = 0;
    window.clearTimeout(failsafe);
    root.classList.remove("theme-switching");
    // Re-announce once the snapshots are gone, in case the browser ignored the first read.
    announceTheme(read());
  };
  // If the browser drops the transition, palette transitions must still come back.
  const failsafe = window.setTimeout(release, 1200);
  const transition = doc.startViewTransition(commit);
  if (wipe) {
    transition.ready
      .then(() => {
        root.animate(
          { clipPath: [WIPE_FROM, WIPE_TO] },
          { duration: 720, easing: "cubic-bezier(.3,.7,.3,1)", pseudoElement: "::view-transition-new(root)" },
        );
      })
      .catch(() => { /* a newer switch won the race */ });
  }
  transition.finished
    .catch(() => undefined)
    .then(release);
}

/** Theme is kept on <html data-theme> so CSS owns the palette; the button only flips it. */
export function useTheme(): [Theme, () => void, Choice] {
  const [theme, setTheme] = useState<Theme>(read);
  const [picked, setPicked] = useState<Choice>(choice);
  useEffect(() => {
    const sync = () => {
      const next = resolve(choice());
      // A theme picked on another tab, or the system flipping while we still follow it.
      if (read() !== next) {
        announceTheme(next);
        animateTheme(() => flushSync(() => { applyTheme(next); setTheme(next); }));
      }
      setTheme(next);
      setPicked(choice());
    };
    const media = query();
    // Coming back from the back/forward cache, or from the app switcher, restores the
    // DOM while the browser toolbar may keep the colour the page had when it left —
    // re-announce the palette so the bars catch up without a reload.
    const repaintChrome = () => announceTheme(resolve(choice()));
    const repaintWhenVisible = () => { if (document.visibilityState === "visible") repaintChrome(); };
    window.addEventListener(EVENT, sync);
    window.addEventListener("storage", sync);
    window.addEventListener("pageshow", repaintChrome);
    window.addEventListener("focus", repaintWhenVisible);
    document.addEventListener("visibilitychange", repaintWhenVisible);
    // Until a theme is picked here, the panel follows the phone/system setting live.
    media?.addEventListener?.("change", sync);
    sync();
    return () => {
      window.removeEventListener(EVENT, sync);
      window.removeEventListener("storage", sync);
      window.removeEventListener("pageshow", repaintChrome);
      window.removeEventListener("focus", repaintWhenVisible);
      document.removeEventListener("visibilitychange", repaintWhenVisible);
      media?.removeEventListener?.("change", sync);
    };
  }, []);
  const toggle = () => {
    const next: Theme = read() === "dark" ? "light" : "dark";
    try { localStorage.setItem(KEY, next); } catch { /* private mode: the choice just won't persist */ }
    // Tell the browser chrome first: the toolbar/status bar should not wait for the
    // snapshots to be taken, which is why mobile browsers used to keep the old colour.
    announceTheme(next);
    animateTheme(() => {
      // Render the new icon inside the snapshot, so it doesn't pop in after the wipe.
      flushSync(() => {
        setTheme(next);
        setPicked(next);
        applyTheme(next);
        window.dispatchEvent(new Event(EVENT));
      });
    });
    window.requestAnimationFrame(() => announceTheme(next));
  };
  return [theme, toggle, picked];
}

export function ThemeToggle({ className }: { className?: string }) {
  const [theme, toggle, picked] = useTheme();
  const dark = theme === "dark";
  const hint = picked === "system" ? " (сейчас как в системе)" : "";
  return (
    <button
      type="button"
      className={className ? `theme-toggle ${className}` : "theme-toggle"}
      onClick={toggle}
      title={`${dark ? "Светлая тема" : "Тёмная тема"}${hint}`}
      aria-label={dark ? "Включить светлую тему" : "Включить тёмную тему"}
      aria-pressed={dark}
    >
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        {dark
          ? <><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></>
          : <path d="M20.5 14.6A8.5 8.5 0 0 1 9.4 3.5a8.5 8.5 0 1 0 11.1 11.1z" />}
      </svg>
    </button>
  );
}