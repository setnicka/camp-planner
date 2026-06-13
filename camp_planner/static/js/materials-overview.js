// Camp Planner — camp-wide materials overview (Phase 4).
//
// Renders one row per catalog material (from the JSON the server inlined in
// #cp-materials-data) with the activity needs that use it, edited in place via the /api
// endpoints — no reloads (except merge, which reloads since the server re-sums needs).
// Edit affordances appear only when data.may_edit; the api re-checks server-side.
"use strict";

(function () {
  const mount = document.getElementById("cp-materials");
  const dataEl = document.getElementById("cp-materials-data");
  if (!mount || !dataEl) return;

  const { el, api, openModal, keyList, toast, toastNext } = window.cpDom;
  const DATA = JSON.parse(dataEl.textContent);
  const U = DATA.urls;
  const mayEdit = DATA.may_edit;
  const MATS = DATA.materials;              // catalog materials with usages; mutated in place
  const COLS = mayEdit ? 6 : 5;             // table columns (for the sub-row colspan)

  const expanded = new Set();               // material ids whose usages are shown (survives row re-render)
  const rowEls = new Map();                 // material id -> { tr, sub, cell } for per-row refresh
  let tbody;                                // stable table body

  // --- small helpers ---------------------------------------------------------
  const withId = (tpl, id) => tpl.replace(/\d+$/, id);                       // swap the trailing 0 sentinel
  const mergeUrl = (id) => U.materialMerge.replace(/\/0\/merge$/, "/" + id + "/merge");  // .../<id>/merge

  // Run a modal's submit: disable its button while it works; on failure re-enable and
  // toast the error. `fn` owns the success path (api call, state update, close(), toast).
  async function submit(btn, fn) {
    btn.disabled = true;
    try { await fn(); }
    catch (e) { btn.disabled = false; toast(e.message, true); }
  }

  const fmtNum = (n) => Number.isInteger(n) ? String(n) : String(Math.round(n * 1000) / 1000);

  // Per-unit totals across a material's usages, grouped by effective unit (need's override or
  // the catalog default), skipping null amounts → "12 ks, 3 m" (units in first-seen order).
  function unitTotals(m) {
    const order = [], sums = new Map();
    for (const u of m.usages) {
      if (u.amount == null) continue;
      const unit = (u.unit || m.unit || "").trim();
      if (!sums.has(unit)) { sums.set(unit, 0); order.push(unit); }
      sums.set(unit, sums.get(unit) + u.amount);
    }
    return order.map((unit) => fmtNum(sums.get(unit)) + (unit ? " " + unit : "")).join(", ");
  }
  const readiness = (m) => ({ ready: m.usages.filter((u) => u.is_ready).length, total: m.usages.length });
  // "Hotovo" cell: a filled badge — green when every usage is ready, red while some remain;
  // a muted dash when the material isn't used anywhere yet.
  function readyBadge(ready, total) {
    if (!total) return el("span", { class: "cp-muted" }, "—");
    const done = ready === total;
    return el("span", { class: "cp-mat-ready " + (done ? "done" : "todo") },
      (done ? "✓ " : "") + ready + "/" + total);
  }
  // map a MaterialNeedOut back onto our flatter usage shape (keep need_id/activity_*)
  const assignNeed = (u, need) => { u.amount = need.amount; u.unit = need.unit; u.note = need.note; u.is_ready = need.is_ready; };

  // --- render ----------------------------------------------------------------
  function buildShell() {
    if (!MATS.length) {
      mount.replaceChildren(el("p", { class: "cp-muted" }, "Zatím žádný materiál — přidej ho z detailu aktivity."));
      return;
    }
    const headRow = el("tr", null,
      el("th", null, "Materiál"), el("th", null, "Jednotka"), el("th", null, "Množství"),
      el("th", null, "Aktivit"), el("th", null, "Hotovo"));
    if (mayEdit) headRow.append(el("th", { class: "cp-actions" }, ""));
    tbody = el("tbody");
    mount.replaceChildren(el("table", { class: "cp-table cp-mat-table" }, el("thead", null, headRow), tbody));
    renderTable();
  }

  function renderTable() {
    tbody.replaceChildren();
    rowEls.clear();
    MATS.slice().sort((a, b) => a.name.localeCompare(b.name, "cs")).forEach((m) => {
      const r = renderMaterialRow(m);
      rowEls.set(m.id, r);
      tbody.append(r.tr, r.sub);
    });
  }

  // Rebuild one material's summary row + usages sub-row in place (totals/readiness recompute).
  // Open/closed state lives in `expanded`, so it survives the swap.
  function refreshRow(m) {
    const old = rowEls.get(m.id);
    const next = renderMaterialRow(m);
    old.tr.replaceWith(next.tr);
    old.sub.replaceWith(next.sub);
    rowEls.set(m.id, next);
  }

  function renderMaterialRow(m) {
    const { ready, total } = readiness(m);
    const open = expanded.has(m.id);
    const tr = el("tr", { class: "cp-mat-row" },
      el("td", null, el("span", { class: "cp-mat-caret" }, open ? "▾" : "▸"), " ", m.name),
      el("td", null, m.unit || "—"),
      el("td", null, unitTotals(m) || "—"),
      el("td", null, String(total)),
      el("td", { class: "cp-mat-hotovo" }, readyBadge(ready, total)));
    if (mayEdit) {
      const edit = el("button", { type: "button", class: "cp-mini", title: "Upravit" }, "✎");
      edit.addEventListener("click", () => openMaterialEdit(m));
      const merge = el("button", { type: "button", class: "cp-mini", title: "Sloučit s jiným" }, "⤳");
      merge.addEventListener("click", () => openMaterialMerge(m));
      const del = el("button", { type: "button", class: "cp-danger cp-mini", title: "Smazat" }, "✕");
      del.addEventListener("click", () => deleteMaterial(m, del));
      tr.append(el("td", { class: "cp-actions" }, edit, merge, del));
    }
    // toggle expand on a row click — but not when clicking the name link or an action button
    tr.addEventListener("click", (e) => {
      if (e.target.closest("a, button")) return;
      if (expanded.has(m.id)) expanded.delete(m.id); else expanded.add(m.id);
      refreshRow(m);
    });
    const cell = el("td", { colspan: String(COLS) });
    const sub = el("tr", { class: "cp-mat-sub" }, cell);
    sub.hidden = !open;
    if (open) renderUsages(m, cell);
    return { tr, sub, cell };
  }

  function renderUsages(m, cell) {
    const wrap = el("div");
    if (m.url) wrap.append(el("div", { class: "cp-mat-note cp-muted" },
      "URL: ", el("a", { href: m.url, target: "_blank", rel: "noopener" }, m.url)));   // catalog url, atop the note
    if (m.note) wrap.append(el("div", { class: "cp-mat-note cp-muted" }, m.note));   // catalog note
    if (!m.usages.length) {
      wrap.append(el("div", { class: "cp-usage-empty cp-muted" }, "Zatím nikde nepoužito."));
    } else {
      const list = el("div", { class: "cp-usage-list" });
      m.usages.forEach((u) => list.append(usageRow(m, u)));
      wrap.append(list);
    }
    cell.replaceChildren(wrap);
  }

  function usageRow(m, u) {
    const cb = el("input", { type: "checkbox" });
    cb.checked = u.is_ready;
    cb.disabled = !mayEdit;
    if (mayEdit) cb.addEventListener("change", async () => {
      try { const j = await api("PATCH", withId(U.needItem, u.need_id), { is_ready: cb.checked }); assignNeed(u, j.need); refreshRow(m); }
      catch (e) { cb.checked = !cb.checked; toast(e.message, true); }
    });
    const qty = ((u.amount != null ? u.amount : "") + " " + (u.unit || m.unit || "")).trim();
    const line = el("div", { class: "cp-usage-line" },
      el("a", { href: withId(U.activityDetail, u.activity_id) + "#materials", class: "cp-usage-act" }, u.activity_title),
      el("span", { class: "cp-muted cp-usage-qty" }, qty));
    if (mayEdit) {
      const edit = el("button", { type: "button", class: "cp-mini", title: "Upravit" }, "✎");
      edit.addEventListener("click", () => openUsageEdit(m, u));
      const del = el("button", { type: "button", class: "cp-danger cp-mini", title: "Odebrat z aktivity" }, "✕");
      del.addEventListener("click", async () => {
        if (!confirm("Odebrat „" + m.name + "“ z aktivity „" + u.activity_title + "“?")) return;
        try { await api("DELETE", withId(U.needItem, u.need_id)); m.usages = m.usages.filter((x) => x.need_id !== u.need_id); refreshRow(m); toast("Odebráno"); }
        catch (e) { toast(e.message, true); }
      });
      line.append(edit, del);
    }
    const main = el("div", { class: "cp-usage-main" }, line);
    if (u.note) main.append(el("div", { class: "cp-muted cp-usage-note" }, u.note));
    return el("div", { class: "cp-usage-row" + (u.is_ready ? " is-ready" : "") }, cb, main);
  }

  // --- edits -----------------------------------------------------------------
  function modalFoot(onOk, okLabel) {
    const cancel = el("button", { type: "button", class: "cp-cancel" }, "Zrušit");
    const ok = el("button", { type: "button", class: "cp-primary" }, okLabel || "Uložit");
    ok.addEventListener("click", () => onOk(ok));
    return { foot: el("div", { class: "cp-modal-foot" }, cancel, ok), cancel, ok };
  }

  function openMaterialEdit(m) {
    const name = el("input", { type: "text", class: "cp-modal-name", value: m.name });
    const unit = el("input", { type: "text", class: "cp-modal-name", value: m.unit || "" });
    const note = el("textarea", { class: "cp-act-textarea", rows: 3 });
    note.value = m.note || "";
    const url = el("input", { type: "url", class: "cp-modal-name", placeholder: "https://…", value: m.url || "" });
    const { foot, cancel } = modalFoot((ok) => {
      const nm = name.value.trim();
      if (!nm) { name.focus(); return; }
      submit(ok, async () => {
        const j = await api("PATCH", withId(U.materialItem, m.id),
          { name: nm, unit: unit.value || null, note: note.value || null, url: url.value || null });
        Object.assign(m, j.material);   // envelope carries no `usages` → m.usages preserved
        close(); renderTable(); toast("Uloženo");   // renderTable re-sorts (name may have changed); expand state survives

      });
    });
    const dialog = el("div", { class: "cp-modal cp-modal-wide" },
      el("div", { class: "cp-modal-head" }, "Upravit materiál"),
      el("div", { class: "cp-pane" },
        el("label", { class: "cp-field-label" }, "Název"), name,
        el("label", { class: "cp-field-label" }, "Výchozí jednotka"), unit,
        el("label", { class: "cp-field-label" }, "Poznámka"), note,
        el("label", { class: "cp-field-label" }, "Odkaz"), url),
      foot);
    const close = openModal(dialog);
    cancel.addEventListener("click", () => close());
    name.focus();
  }

  function openUsageEdit(m, u) {
    // shared dialog (cpMaterialNeedEdit) — same edit window as the activity detail page
    window.cpMaterialNeedEdit({
      title: m.name + " — " + u.activity_title, need: u, defaultUnit: m.unit,
      url: withId(U.needItem, u.need_id),
      onSaved: (need) => { assignNeed(u, need); refreshRow(m); },
    });
  }

  function deleteMaterial(m, btn) {
    if (!confirm("Smazat „" + m.name + "“ z katalogu?")) return;
    btn.disabled = true;   // guard against a double-click while the request is in flight
    // the server rejects (400) a material still used by activities → surfaced as an error toast
    api("DELETE", withId(U.materialItem, m.id))
      .then(() => {
        const i = MATS.findIndex((x) => x.id === m.id);
        if (i >= 0) MATS.splice(i, 1);
        expanded.delete(m.id); rowEls.delete(m.id);
        renderTable();
        toast("Smazáno");
      })
      .catch((e) => { btn.disabled = false; toast(e.message, true); });
  }

  // Merge this material INTO another (picked from the rest of the catalog, fuzzy). The server
  // migrates/sums the needs and deletes the source, so we reload rather than reconcile locally.
  function openMaterialMerge(m) {
    const others = MATS.filter((x) => x.id !== m.id);
    if (!others.length) { toast("Není do čeho slučovat — v katalogu je jen tento materiál.", true); return; }
    const search = el("input", { type: "text", class: "cp-modal-search", placeholder: "Sloučit do…" });
    const list = el("div", { class: "cp-modal-list" });
    const cancel = el("button", { type: "button", class: "cp-cancel" }, "Zrušit");
    const dialog = el("div", { class: "cp-modal" },
      el("div", { class: "cp-modal-head" }, "Sloučit „" + m.name + "“ do…"),
      el("div", { class: "cp-pane" },
        el("p", { class: "cp-muted" }, "Použití se přesunou do vybraného materiálu a tento se smaže."),
        search, list),
      el("div", { class: "cp-modal-foot" }, cancel));
    const close = openModal(dialog);
    cancel.addEventListener("click", () => close());

    // keyboard-navigable list (cpDom.keyList); picking confirms then merges
    const setRows = keyList(search);
    let merging = false;   // guard against a second pick while the merge + reload is in flight
    function pick(t) {
      if (merging) return;
      if (!confirm("Sloučit „" + m.name + "“ do „" + t.name + "“? (všechny výskyty „" + m.name + "“ budou změněny na „" + t.name + "“)")) return;
      merging = true;
      api("POST", mergeUrl(m.id), { into: t.id })
        .then(() => { close(); toastNext("Sloučeno do „" + t.name + "“"); location.reload(); })
        .catch((e) => { merging = false; toast(e.message, true); });
    }
    function renderResults() {
      const q = search.value.trim();
      const matches = q && window.cpFuzzy ? window.cpFuzzy.filter(q, others, (x) => x.name) : others;
      const entries = matches.map((t) => ({
        el: el("button", { type: "button", class: "cp-modal-item" },
          el("span", null, t.name), t.unit ? el("span", { class: "cp-modal-recent" }, t.unit) : null),
        pick: () => pick(t),
      }));
      list.replaceChildren(...entries.map((e) => e.el));
      if (!entries.length) list.append(el("div", { class: "cp-muted" }, "Nic nenalezeno."));
      setRows(entries);
    }
    search.addEventListener("input", renderResults);
    renderResults();
    search.focus();
  }

  // arriving from an activity's material list (#material-<id>): open that row, scroll to it,
  // and flash it. Added to `expanded` before buildShell so it renders open from the start.
  const hashMatch = /^#material-(\d+)$/.exec(location.hash);
  const hashId = hashMatch ? Number(hashMatch[1]) : null;
  if (hashId != null) expanded.add(hashId);

  buildShell();

  if (hashId != null) {
    const r = rowEls.get(hashId);
    if (r) {
      r.tr.scrollIntoView({ block: "center" });
      r.tr.classList.add("cp-mat-hl");
      setTimeout(() => r.tr.classList.remove("cp-mat-hl"), 2000);
    }
  }
})();
