// Horizontal scrollers (tables, the phone sidebar, the chat toolbar) hide part
// of their content off-screen. On a phone that is easy to miss, so every such
// element gets a soft fade on the edge where more content is hidden — the fade
// disappears as soon as the user reaches that end.
import { useEffect } from "react";

const LEFT = "x-fade-left";
const RIGHT = "x-fade-right";

function paint(el: HTMLElement) {
  const hidden = el.scrollWidth - el.clientWidth;
  if (hidden <= 1) {
    el.classList.remove(LEFT, RIGHT);
    return;
  }
  // The first column of a mobile table is sticky: nothing is hidden on its left,
  // and fading it would dim the column the user is reading.
  const stickyLeft = el.dataset.slot === "table-container";
  el.classList.toggle(LEFT, !stickyLeft && el.scrollLeft > 1);
  el.classList.toggle(RIGHT, el.scrollLeft < hidden - 1);
}

function scrollsSideways(el: HTMLElement) {
  const overflowX = getComputedStyle(el).overflowX;
  return overflowX === "auto" || overflowX === "scroll";
}

function scan() {
  for (const el of document.querySelectorAll<HTMLElement>("body *")) {
    const hidden = el.scrollWidth - el.clientWidth;
    if (hidden > 1 && scrollsSideways(el)) paint(el);
    else if (el.classList.contains(LEFT) || el.classList.contains(RIGHT)) el.classList.remove(LEFT, RIGHT);
  }
}

/** Mount once, in the app shell. */
export function useScrollFades() {
  useEffect(() => {
    let frame = 0;
    const schedule = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => { frame = 0; scan(); });
    };
    const onScroll = (event: Event) => {
      if (event.target instanceof HTMLElement) paint(event.target);
    };

    scan();
    // Pages, tabs and dialogs mount their tables after the first render, and
    // expanding a header or opening the phone menu only toggles a class.
    const observer = new MutationObserver(schedule);
    observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["class", "style", "hidden"] });
    document.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", schedule);
    window.addEventListener("orientationchange", schedule);
    void document.fonts?.ready.then(schedule);

    return () => {
      observer.disconnect();
      document.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", schedule);
      window.removeEventListener("orientationchange", schedule);
      if (frame) cancelAnimationFrame(frame);
    };
  }, []);
}
