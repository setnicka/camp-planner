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

  // Fetch a fresh token into the <meta> tag (which every consumer re-reads), so a page left
  // open past the server's token limit doesn't fail its next mutation. No-op without a refresh
  // URL; concurrent calls share one in-flight fetch.
  let refreshing = null;
  function csrfRefresh() {
    if (refreshing) return refreshing;
    const url = document.querySelector('meta[name="csrf-refresh"]')?.content;
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (!url || !meta) return Promise.resolve(false);
    refreshing = fetch(url)
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => { if (j?.csrf_token) { meta.content = j.csrf_token; return true; } return false; })
      .catch(() => false)
      .finally(() => { refreshing = null; });
    return refreshing;
  }

  // JSON call against an /api endpoint: attaches the CSRF header, JSON-encodes `body` when
  // given, parses the {ok, …} envelope and throws Error(json.error) on failure (so callers
  // just try/catch). Returns the parsed JSON on success. On an expired token, refresh + retry once.
  async function api(method, url, body, _retried) {
    const opts = { method, headers: { "X-CSRFToken": csrf() } };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const resp = await fetch(url, opts);
    const json = await resp.json().catch(() => ({}));
    if (resp.status === 400 && /csrf/i.test(json.error || "") && !_retried && (await csrfRefresh())) {
      return api(method, url, body, true);
    }
    if (!resp.ok || !json.ok) {
      const err = new Error(json.error || "Operace selhala.");
      err.status = resp.status;   // lets callers branch on e.g. a 409 conflict
      throw err;
    }
    return json;
  }

  // Item-scoped api URL templates carry a `0` sentinel the client swaps for the real id.
  const withId = (tpl, id) => tpl.replace(/\d+$/, id);
  // Merge URLs end …/0/merge — swap the sentinel inside.
  const mergeUrl = (tpl, id) => tpl.replace(/\/0\/merge$/, "/" + id + "/merge");

  // Refresh proactively, well before the token's server-side limit, so the reactive retry
  // above (which also covers laptop sleep) stays a rare fallback.
  if (document.querySelector('meta[name="csrf-refresh"]')) {
    setInterval(csrfRefresh, 30 * 60 * 1000);
  }

  // A small colored square (category color, etc.); falls back to grey when the color is unset.
  const swatch = (color) => el("span", { class: "cp-swatch", style: "background:" + (color || "var(--cp-text-dim)") });

  // Body-level portals sit outside the element carrying data-cp-theme; the palette
  // reaches them via :has() but color-scheme does not, leaving native widgets light in a
  // dark dialog — so stamp the theme onto the portal itself (no attribute = light =
  // stamp nothing). theme.js re-stamps open portals on a switch.
  function stampTheme(node) {
    const t = document.querySelector("[data-cp-theme]");
    if (t) node.setAttribute("data-cp-theme", t.getAttribute("data-cp-theme"));
    return node;
  }

  // Mount a dialog inside a backdrop overlay: Escape / backdrop-click dismiss it, Tab is
  // trapped inside, focus returns to the opener, onClose runs once on any dismissal.
  // `confirmClose` (optional) guards only Escape/backdrop — return false to keep the
  // dialog open; the returned close() is always unconditional.
  function openModal(dialog, onClose, { confirmClose } = {}) {
    const opener = document.activeElement;
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    const overlay = stampTheme(el("div", { class: "cp-modal-overlay" }, dialog));
    let closed = false;
    const close = () => {
      if (closed) return;
      closed = true;
      overlay.remove();
      document.removeEventListener("keydown", onKey);
      if (opener && document.contains(opener)) opener.focus();
      if (onClose) onClose();
    };
    const dismiss = () => { if (!confirmClose || confirmClose()) close(); };
    const focusables = () => [...overlay.querySelectorAll(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    )].filter((n) => !n.disabled && n.offsetParent !== null);
    const onKey = (e) => {
      if (e.key === "Escape") { dismiss(); return; }
      if (e.key !== "Tab") return;
      const f = focusables();          // keep Tab cycling inside the dialog
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    overlay.addEventListener("click", (e) => { if (e.target === overlay) dismiss(); });
    document.addEventListener("keydown", onKey);
    document.body.append(overlay);
    return close;
  }

  // Run a modal's submit: disable the button while `fn` is in flight (double-submit
  // guard), toast a failure, re-enable afterwards.
  async function submit(btn, fn) {
    btn.disabled = true;
    try { await fn(); }
    catch (e) { toast(e.message, true); }
    finally { btn.disabled = false; }
  }

  // Standard form dialog: title + pane + Zrušit/OK footer. Owns the submit cycle and asks
  // before an Escape / backdrop-click discards edited input; onSubmit(close) closes itself.
  function formModal({ title, pane, okLabel = "Uložit", onSubmit, onClose }) {
    const cancel = el("button", { type: "button", class: "cp-cancel" }, "Zrušit");
    const ok = el("button", { type: "button", class: "cp-primary" }, okLabel);
    const dialog = el("div", { class: "cp-modal cp-modal-wide" },
      el("div", { class: "cp-modal-head" }, title),
      pane,
      el("div", { class: "cp-modal-foot" }, cancel, ok));
    let dirty = false;
    pane.addEventListener("input", () => { dirty = true; });
    // chip toggles are buttons, not inputs — a click on one is an edit too
    pane.addEventListener("click", (e) => { if (e.target.closest(".cp-cat-chip")) dirty = true; });
    const close = openModal(dialog, onClose, {
      confirmClose: () => !dirty || window.confirm("Zavřít bez uložení?"),
    });
    cancel.addEventListener("click", () => close());
    ok.addEventListener("click", () => submit(ok, () => onSubmit(close)));
  }

  // Fuzzy search-and-pick modal over a list. labelOf(item) feeds the row label and the
  // filter; metaOf (optional) adds a right-side hint. onPick(item, close) decides itself
  // when to close (merge only closes after its confirm + api call succeeds). extraEntry(q)
  // (optional) may return { label, pick } appended as a synthetic "+ Vytvořit …" row.
  function searchPicker({ title, hint, placeholder = "Hledat…", items, labelOf, metaOf,
                          onPick, extraEntry, empty = "Nic nenalezeno." }) {
    const search = el("input", { type: "text", class: "cp-modal-search", placeholder });
    const list = el("div", { class: "cp-modal-list" });
    const cancel = el("button", { type: "button", class: "cp-cancel" }, "Zrušit");
    const dialog = el("div", { class: "cp-modal" },
      el("div", { class: "cp-modal-head" }, title),
      el("div", { class: "cp-pane" }, hint ? el("p", { class: "cp-muted" }, hint) : null, search, list),
      el("div", { class: "cp-modal-foot" }, cancel));
    const close = openModal(dialog);
    cancel.addEventListener("click", () => close());
    const setRows = keyList(search);
    function rerender() {
      const q = search.value.trim();
      const matches = q && window.cpFuzzy ? window.cpFuzzy.filter(q, items, labelOf) : items;
      const entries = matches.map((it) => {
        const meta = metaOf && metaOf(it);
        return {
          el: el("button", { type: "button", class: "cp-modal-item" },
            el("span", null, labelOf(it)), meta ? el("span", { class: "cp-modal-recent" }, meta) : null),
          pick: () => onPick(it, close),
        };
      });
      const extra = extraEntry && extraEntry(q);
      if (extra) entries.push({
        el: el("button", { type: "button", class: "cp-modal-item" }, extra.label),
        pick: () => extra.pick(close),
      });
      list.replaceChildren(...entries.map((e) => e.el));
      if (!entries.length) list.append(el("div", { class: "cp-muted" }, empty));
      setRows(entries);
    }
    search.addEventListener("input", rerender);
    rerender();
    search.focus();
  }

  // Merge-into flow on top of searchPicker: pick the target, confirm, POST {into: id},
  // then reload (the server re-sums, so local reconciliation isn't attempted).
  function mergePicker({ title, hint, items, labelOf, metaOf, url, confirmText, successText }) {
    let merging = false;   // guard against a second pick while the merge + reload is in flight
    searchPicker({
      title, hint, items, labelOf, metaOf, placeholder: "Sloučit do…",
      onPick: (t, close) => {
        if (merging || !window.confirm(confirmText(t))) return;
        merging = true;
        api("POST", url, { into: t.id })
          .then(() => { close(); toastNext(successText(t)); location.reload(); })
          .catch((e) => { merging = false; toast(e.message, true); });
      },
    });
  }

  // Column-header org filter: a "Vše ▾ / Orgové (n) ▾" button opening a checkbox panel.
  // Toggles mutate the caller-owned `selected` Set in place, then onChange() fires.
  // `extra` (optional) prepends a page-specific checkbox { label, checked, set(v),
  // countInLabel }. Outside clicks and Escape close the open panel. Returns { th,
  // setLabel } — setLabel(n) lets freezeColumns size the column to the widest label.
  let openPopover = null;
  document.addEventListener("click", () => {
    if (openPopover) { openPopover.hidden = true; openPopover = null; }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && openPopover) { openPopover.hidden = true; openPopover = null; }
  });
  function orgFilterHead({ orgs, selected, extra, onChange }) {
    const btn = el("button", { type: "button", class: "cp-th-filter cp-th-dd-btn" });
    let extraCb = null;
    const count = () => selected.size + (extra?.countInLabel && extraCb?.checked ? 1 : 0);
    const setLabel = (n = count()) => { btn.textContent = n ? "Orgové (" + n + ") ▾" : "Vše ▾"; };
    const panel = el("div", { class: "cp-th-pop", hidden: true });
    panel.addEventListener("click", (e) => e.stopPropagation());   // keep clicks inside from closing it
    if (extra) {
      extraCb = el("input", { type: "checkbox" });
      extraCb.checked = !!extra.checked;
      extraCb.addEventListener("change", () => { extra.set(extraCb.checked); setLabel(); onChange(); });
      panel.append(el("label", { class: "cp-th-pop-row cp-th-pop-opt" }, extraCb, " " + extra.label));
    }
    if (!orgs.length) panel.append(el("div", { class: "cp-muted" }, "Žádní orgové."));
    orgs.forEach((o) => {
      const cb = el("input", { type: "checkbox" });
      cb.checked = selected.has(o.id);
      cb.addEventListener("change", () => {
        if (cb.checked) selected.add(o.id); else selected.delete(o.id);
        setLabel(); onChange();
      });
      panel.append(el("label", { class: "cp-th-pop-row" }, cb, " ", o.initials, " – ", o.name));
    });
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const show = panel.hidden;
      if (openPopover && openPopover !== panel) openPopover.hidden = true;
      panel.hidden = !show;
      openPopover = show ? panel : null;
    });
    setLabel();
    const th = el("th", null, el("span", { class: "cp-th-label" }, "Orgové"), el("div", { class: "cp-th-dd" }, btn, panel));
    return { th, setLabel };
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
        // mousedown (not click) + preventDefault: a blur-triggered re-render can detach
        // the row before a click would land; mousedown fires first and keeps the focus.
        r.el.addEventListener("mousedown", (e) => { e.preventDefault(); r.pick(); });
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
    if (!stack) { stack = stampTheme(el("div", { id: "cp-toasts", class: "cp-toasts" })); document.body.append(stack); }
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
  // Tab selection persisted in the URL hash as the first `&`-segment (e.g. #todos&done=1), so it
  // can coexist with a page's filter params that follow it. write() swaps only the tab token and
  // preserves the trailing segments, so a page's filters survive switching tabs and back.
  function tabHash(validKeys) {
    const segs = location.hash.slice(1).split("&");
    return {
      initial: validKeys.includes(segs[0]) ? segs[0] : null,
      write: (key) => {
        const rest = location.hash.slice(1).split("&").slice(1);
        history.replaceState(null, "", "#" + [key, ...rest].join("&"));
      },
    };
  }

  // Freeze a table's current column widths into a <colgroup> + table-layout:fixed, so later
  // re-renders (e.g. filtering to fewer rows) keep the columns put instead of reflowing/jumping.
  // Call after a full-data paint so the frozen widths fit the widest content. No-op when the
  // table is hidden (zero-width) — there's nothing to measure yet. Shared by the overview tables.
  function freezeColumns(table, headRow) {
    const widths = [...headRow.children].map((th) => th.getBoundingClientRect().width);
    if (!widths.some((w) => w > 0)) return;
    const colgroup = el("colgroup");
    widths.forEach((w) => { const c = el("col"); c.style.width = Math.round(w) + "px"; colgroup.append(c); });
    table.insertBefore(colgroup, table.firstChild);
    table.style.tableLayout = "fixed";
    table.style.width = Math.round(widths.reduce((a, b) => a + b, 0)) + "px";
  }

  return { el, csrf, csrfRefresh, api, withId, mergeUrl, swatch, openModal, submit, formModal,
           searchPicker, mergePicker, orgFilterHead, chipGroup, keyList, toast, toastNext, flash,
           plural, tabHash, freezeColumns };
})();
