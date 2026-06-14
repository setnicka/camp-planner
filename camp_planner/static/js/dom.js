// Camp Planner — shared frontend primitives.
//
// Tiny DOM/UI helpers reused across the inline-editing pages (settings, timeline
// editor, activity detail). Exposed as window.cpDom; load this before any script
// that destructures from it. No build step — plain globals.
"use strict";

window.cpDom = (function () {
  // el(tag, attrs, ...kids): build an element. `class` → className; a key that is a
  // node property is assigned, otherwise set as an attribute; null/undefined kids skipped.
  function el(tag, attrs, ...kids) {
    const node = document.createElement(tag);
    for (const k in attrs || {}) {
      if (k === "class") node.className = attrs[k];
      else if (k in node) node[k] = attrs[k];
      else node.setAttribute(k, attrs[k]);
    }
    for (const kid of kids) if (kid != null) node.append(kid);
    return node;
  }

  const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content ?? "";

  // JSON call against an /api endpoint: attaches the CSRF header, JSON-encodes `body` when
  // given, parses the {ok, …} envelope and throws Error(json.error) on failure (so callers
  // just try/catch). Returns the parsed JSON on success.
  async function api(method, url, body) {
    const opts = { method, headers: { "X-CSRFToken": csrf() } };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const resp = await fetch(url, opts);
    const json = await resp.json().catch(() => ({}));
    if (!resp.ok || !json.ok) throw new Error(json.error || "Operace selhala.");
    return json;
  }

  // A small colored square (category color, etc.); falls back to grey when the color is unset.
  const swatch = (color) => el("span", { class: "cp-swatch", style: "background:" + (color || "#9e9e9e") });

  // Mount a dialog inside a backdrop overlay; wires Escape, backdrop-click and teardown
  // once for every modal. onClose (if given) runs exactly once on any dismissal. Returns
  // the idempotent close() so callers can also dismiss programmatically (e.g. on success).
  function openModal(dialog, onClose) {
    const overlay = el("div", { class: "cp-modal-overlay" }, dialog);
    let closed = false;
    const close = () => {
      if (closed) return;
      closed = true;
      overlay.remove();
      document.removeEventListener("keydown", onKey);
      if (onClose) onClose();
    };
    const onKey = (e) => { if (e.key === "Escape") close(); };
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
    document.addEventListener("keydown", onKey);
    document.body.append(overlay);
    return close;
  }

  // Selectable chip set (`.cp-cat-chips`). Each entry is [value, ...chipChildren]; clicking
  // toggles the `on` class. Returns { node, get() } — get() yields the selected value, or
  // (multi) the array of selected values. Used by the role / category / org pickers.
  function chipGroup(entries, { multi = false, selected } = {}) {
    const node = el("div", { class: "cp-cat-chips" });
    const els = new Map();
    const sel = multi ? new Set(selected || []) : { v: selected };
    const isOn = (v) => multi ? sel.has(v) : sel.v === v;
    const sync = () => els.forEach((chip, v) => chip.classList.toggle("on", isOn(v)));
    for (const [value, ...kids] of entries) {
      const chip = el("button", { type: "button", class: "cp-cat-chip" }, ...kids);
      chip.addEventListener("click", () => {
        if (multi) { sel.has(value) ? sel.delete(value) : sel.add(value); } else { sel.v = value; }
        sync();
      });
      els.set(value, chip);
      node.append(chip);
    }
    sync();
    return { node, get: () => multi ? [...sel] : sel.v };
  }

  // Keyboard navigation for a search-box + results list (the activity / material pickers):
  // ↑/↓ move the highlight, Enter picks the active row, hover syncs it. Create once with the
  // search input; after each (re)render call the returned setRows(entries) with the rows in
  // display order — entries are { el, pick } and it wires click + hover and highlights the
  // first. The active row carries the `cp-active` class (styled in components.css).
  function keyList(search) {
    let rows = [], active = -1;
    const setActive = (i) => {
      if (rows[active]) rows[active].el.classList.remove("cp-active");
      active = Math.max(-1, Math.min(i, rows.length - 1));
      if (rows[active]) { rows[active].el.classList.add("cp-active"); rows[active].el.scrollIntoView({ block: "nearest" }); }
    };
    search.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { e.preventDefault(); setActive(active + 1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); setActive(active - 1); }
      else if (e.key === "Enter" && rows[active]) { e.preventDefault(); rows[active].pick(); }
    });
    return function setRows(entries) {
      rows = entries;
      entries.forEach((r, i) => {
        r.el.addEventListener("click", r.pick);
        r.el.addEventListener("mousemove", () => setActive(i));
      });
      active = -1;
      setActive(0);   // highlight the first so Enter works straight after typing
    };
  }

  // Floating "toast" notification stacked at the top-right of the viewport (above modals).
  // Used for AJAX save/error feedback. Success toasts dwell briefly; errors stay longer.
  // Click to dismiss early. The stack container is created on first use.
  function toast(message, isError) {
    let stack = document.getElementById("cp-toasts");
    if (!stack) { stack = el("div", { id: "cp-toasts", class: "cp-toasts" }); document.body.append(stack); }
    const box = el("div", { class: "cp-toast" + (isError ? " cp-toast-error" : "") }, message);
    const dismiss = () => {
      box.classList.remove("cp-toast-show");
      box.addEventListener("transitionend", () => box.remove(), { once: true });
    };
    box.addEventListener("click", dismiss);
    stack.append(box);
    void box.offsetWidth;            // commit opacity:0 so adding the class transitions in
    box.classList.add("cp-toast-show");
    setTimeout(dismiss, isError ? 6000 : 3000);
    return box;
  }

  // Queue a toast to appear after the next full page load — for flows that reload the page
  // (e.g. the timeline save), where an immediate toast would be wiped by the navigation.
  function toastNext(message, isError) {
    try { sessionStorage.setItem("cp-toast", JSON.stringify({ message, isError: !!isError })); } catch (_e) { /* sessionStorage unavailable */ }
  }
  // On load, surface any toast queued by toastNext() before a reload, then clear it.
  function drainQueuedToast() {
    let raw;
    try { raw = sessionStorage.getItem("cp-toast"); if (raw) sessionStorage.removeItem("cp-toast"); } catch (_e) { return; }
    if (!raw) return;
    try { const t = JSON.parse(raw); toast(t.message, t.isError); } catch (_e) { /* malformed — ignore */ }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", drainQueuedToast);
  else drainQueuedToast();

  // Transient banner in `area` (a [data-flash] container): fades in, dwells, fades out.
  function flash(area, message, isError) {
    if (!area) return;
    const banner = el("div", { class: "cp-flash" + (isError ? " cp-flash-error" : "") }, message);
    area.replaceChildren(banner);
    void banner.offsetWidth; // commit opacity:0 so adding the class transitions
    banner.classList.add("cp-flash-show");
    setTimeout(() => {
      banner.classList.remove("cp-flash-show");
      banner.addEventListener("transitionend", () => banner.remove(), { once: true });
    }, 8000);
  }

  // Czech plural agreement: 1 → one, 2–4 → few, 5+/0 → many. e.g. plural(n, "změna","změny","změn").
  const plural = (n, one, few, many) => (n === 1 ? one : (n >= 2 && n <= 4 ? few : many));

  // Small tab↔URL-hash controller shared by the tabbed pages (camp settings, activity detail).
  // Reads the active tab from location.hash on load (validated against validKeys) and writes it
  // back on change with replaceState — shareable/reloadable links, no scroll-jump, no history
  // spam. Returns { initial, write }: initial is the hashed key if valid else null (the caller
  // picks its own default); write(key) updates the hash.
  function tabHash(validKeys) {
    const fromHash = location.hash.slice(1);
    return {
      initial: validKeys.includes(fromHash) ? fromHash : null,
      write: (key) => history.replaceState(null, "", "#" + key),
    };
  }

  return { el, csrf, api, swatch, openModal, chipGroup, keyList, toast, toastNext, flash, plural, tabHash };
})();
