// The section tabs sit in a strip that scrolls sideways on a phone (content.css).
(function () {
  "use strict";

  const strip = document.querySelector(".cp-camp-links");
  if (!strip) return;

  // Scrolls the strip and nothing else: scrollIntoView would also scroll the document,
  // undoing the position a Back navigation has just restored.
  const active = strip.querySelector(".cp-camp-link.is-active");
  function centre() {
    if (!active) return;
    const tab = active.getBoundingClientRect();
    const box = strip.getBoundingClientRect();
    strip.scrollLeft += tab.left - box.left - (box.width - tab.width) / 2;   // centred, then clamped
  }

  // A word cut off at the edge says nothing about a strip that scrolls, so fade whichever
  // end still has sections behind it (content.css draws the fade).
  let queued = false;
  let scrollable = false;
  function fade() {
    queued = false;
    const max = strip.scrollWidth - strip.clientWidth;
    // Load, and turning a phone from landscape to portrait, are where a wrapped row first
    // becomes a strip; centre then, never while the reader is scrolling one that already was.
    if (max > 0 && !scrollable) centre();
    scrollable = max > 0;
    const left = strip.scrollLeft;
    strip.classList.toggle("cp-more-left", left > 2);
    strip.classList.toggle("cp-more-right", max - left > 2);
  }
  // Both events arrive in bursts (a desktop drag, an iOS URL bar collapsing mid-scroll),
  // so the reads run once a frame.
  function queue() {
    if (queued) return;
    queued = true;
    requestAnimationFrame(fade);
  }
  strip.addEventListener("scroll", queue, { passive: true });
  window.addEventListener("resize", queue);
  fade();
})();

// A <details> stays open until its own button is clicked again; close it the way a menu does.
(function () {
  "use strict";

  const menu = document.querySelector(".cp-account-menu");
  if (!menu) return;

  // pointerdown, not click: iOS withholds document-level clicks for taps that land on
  // nothing interactive. Capture, because the tables' column popovers stop propagation (dom.js).
  document.addEventListener("pointerdown", (e) => {
    if (menu.open && !menu.contains(e.target)) menu.open = false;
  }, true);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && menu.open) {
      menu.open = false;
      menu.querySelector("summary").focus();
    }
  });
})();
