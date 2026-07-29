// Colour-theme switch (light / auto / dark). Writes data-cp-theme on the embedded wrapper
// if there is one, else on <html>; only a clicked light/dark is stored. The pre-paint
// re-apply lives inline in the shells (full.html, page.html) — this file only wires the
// control. A host pinning
// the theme in its own markup is invisible to the server, so detect it here and hide:
// a nearer ancestor beats anything we set, and a dead switch is worse than none.
(function () {
  "use strict";

  const KEY = "cp-theme";
  const ATTR = "data-cp-theme";
  const POS = { light: 0, auto: 1, dark: 2 };   // knob position on the track
  const group = document.querySelector("[data-cp-theme-switch]");
  if (!group) return;
  const root = document.documentElement;
  // Embedded, keep the attribute inside our own subtree: <html> is the host's, and the
  // palette reaches the body-level modal/toast portals through :has() either way.
  const target = document.querySelector(".cp-embed") || root;

  // A host pin, i.e. the attribute on something that is neither <html> nor our own
  // wrapper. Clear <html> as we go: a value left there by an earlier visit would win in
  // the body-level modal/toast portals, which sit outside the host's wrapper.
  if (document.querySelector(`[${ATTR}]:not(:root):not(.cp-embed)`)) {
    root.removeAttribute(ATTR);
    group.hidden = true;
    return;
  }

  function apply(mode, persist) {
    const explicit = mode === "light" || mode === "dark";
    const active = explicit ? mode : "auto";
    target.setAttribute(ATTR, active);
    // Open body-level portals carry the theme themselves (dom.js stamps new ones).
    for (const p of document.querySelectorAll(".cp-modal-overlay, #cp-toasts")) {
      p.setAttribute(ATTR, active);
    }
    if (persist) {   // only a click is a choice — never store the fallback
      try {
        if (explicit) localStorage.setItem(KEY, mode);
        else localStorage.removeItem(KEY);
      } catch (e) { /* storage disabled → not remembered, but the page still switches */ }
    }

    group.style.setProperty("--cp-theme-pos", POS[active]);
    for (const btn of group.querySelectorAll("button[data-theme]")) {
      const on = btn.dataset.theme === active;
      btn.classList.toggle("on", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
    }
    // Anything that baked a token into JS-generated markup must re-derive it (the
    // timeline's day/night overlay does — vis strips var() from an item's style).
    window.dispatchEvent(new CustomEvent("cp:themechange", { detail: { mode: active } }));
  }

  group.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-theme]");
    if (btn) apply(btn.dataset.theme, true);
  });

  // Nothing stored yet: standalone follows the OS, embedded stays light. The host's page
  // decides there and we cannot read its background, so defaulting to auto would turn our
  // whole embed dark for every dark-OS visitor without the host asking for it. Choosing
  // "auto" from the switch still works — that's the visitor overriding, not us guessing.
  const fallback = target === root ? "auto" : "light";
  let saved = null;
  try {
    saved = localStorage.getItem(KEY);
  } catch (e) { /* unreadable → fall back below */ }
  apply(saved || fallback);   // anything that isn't "light"/"dark" normalises to auto
  group.classList.add("is-ready");   // from here on the knob animates between positions
})();
