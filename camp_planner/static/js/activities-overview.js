// Camp Planner — camp-wide activity overview / status page (Phases 5+6).
//
// Renders every activity (from the JSON the server inlined in #cp-overview-data) in one
// table: category, orgs, todo/material progress, a column per pinned tag, and slot counts.
// Filtering and sorting are driven entirely from the column headers — each header carries its
// own sort toggle and/or filter control. Delete and merge go to the /api endpoints; merge
// reloads (the server moves slots/needs across activities). Edit affordances appear only when
// data.may_edit; the api re-checks server-side.
"use strict";

(function () {
  const mount = document.getElementById("cp-overview");
  const dataEl = document.getElementById("cp-overview-data");
  if (!mount || !dataEl) return;

  const { el, api, swatch, openModal, keyList, toast, toastNext } = window.cpDom;
  const DATA = JSON.parse(dataEl.textContent);
  const U = DATA.urls;
  const mayEdit = DATA.may_edit;
  const ROWS = DATA.activities;             // mutated in place (delete splices; merge reloads)
  const CATEGORIES = DATA.categories;
  const ORGS = DATA.orgs;
  const PINNED = DATA.pinned_tags;          // [{id, name, kind}] — table columns + filter/sort

  const withId = (tpl, id) => tpl.replace(/\d+$/, id);                       // swap the trailing 0 sentinel
  const mergeUrl = (id) => U.activityMerge.replace(/\/0\/merge$/, "/" + id + "/merge");  // .../<id>/merge
  const plural = (n, one, few, many) => (n === 1 ? one : n >= 2 && n <= 4 ? few : many);
  const clampPct = (v) => Math.max(0, Math.min(100, parseInt(v, 10) || 0));
  const slotCount = (r) => r.slots.main + r.slots.prep + r.slots.cleanup;
  const hasTag = (r, id) => Object.prototype.hasOwnProperty.call(r.tags, id);   // key present = tag applies
  const colCount = () => 6 + PINNED.length + (mayEdit ? 1 : 0);

  // filter + sort state (cleared by resetFilters → buildShell, which rebuilds the controls)
  const filter = { categoryId: null, unfinishedTodos: false, unfinishedMaterials: false,
                   orgIds: new Set(), garantsOnly: false, tags: new Map() };   // tags: tagId -> "has"|"checked"|"unchecked"
  let sortKey = "title";
  let sortDir = 1;                // 1 = the column's natural order, -1 = reversed (second click)
  let tbody, countLabel;
  const sortArrows = new Map();   // sortKey -> the direction indicator span, updated in place on sort change
  const arrowFor = (key) => (key === sortKey ? (sortDir === 1 ? " ▾" : " ▴") : "");
  let openPopover = null;         // the currently-open org dropdown panel (closed on outside click)

  // --- cell renderers --------------------------------------------------------
  function slotText(r) {
    const { main, prep, cleanup } = r.slots;
    if (!main && !prep && !cleanup) return "—";
    let s = main + " " + plural(main, "slot", "sloty", "slotů");
    const extra = [];
    if (prep) extra.push("+" + prep + " " + plural(prep, "příprava", "přípravy", "příprav"));
    if (cleanup) extra.push("+" + cleanup + " " + plural(cleanup, "úklid", "úklidy", "úklidů"));
    if (extra.length) s += " (" + extra.join(", ") + ")";
    return s;
  }

  function orgCell(r) {
    if (!r.garants.length && !r.helpers.length) return el("td", { class: "cp-muted" }, "—");
    const td = el("td", { class: "cp-ov-orgs" });
    const parts = r.garants.map((i) => el("span", { class: "cp-ov-garant" }, i))
      .concat(r.helpers.map((i) => el("span", { class: "cp-ov-helper" }, i)));
    parts.forEach((p, i) => { if (i) td.append(", "); td.append(p); });
    return td;
  }

  function progressCell(c) {
    if (!c.total) return el("td", { class: "cp-muted cp-ov-num" }, "—");
    const cls = c.done < c.total ? " cp-ov-unfinished" : " cp-ov-done";
    return el("td", { class: "cp-ov-num" + cls }, c.done + "/" + c.total);
  }

  // one pinned-tag cell for an activity: not applied → "—"; else rendered per the tag's kind.
  function tagCell(tag, r) {
    if (!hasTag(r, tag.id)) return el("td", { class: "cp-ov-tag cp-muted" }, "—");
    const value = r.tags[tag.id];
    if (tag.kind === "check") {
      const on = value === "true";
      return el("td", { class: "cp-ov-tag" }, el("span", { class: "cp-ov-check " + (on ? "yes" : "no") }, on ? "✓" : "✗"));
    }
    if (tag.kind === "progress") {
      const pct = clampPct(value);
      return el("td", { class: "cp-ov-tag" },
        el("span", { class: "cp-ov-bar" },
          el("span", { class: "cp-ov-bar-fill", style: "width:" + pct + "%" }),
          el("span", { class: "cp-ov-bar-num" }, pct + " %")));
    }
    if (tag.kind === "text") return el("td", { class: "cp-ov-tag" }, value || "—");
    return el("td", { class: "cp-ov-tag" }, "✓");   // label: presence only
  }

  function actionCell(r) {
    const merge = el("button", { type: "button", class: "cp-mini", title: "Sloučit s jinou aktivitou" }, "⤳");
    merge.addEventListener("click", () => openMerge(r));
    const del = el("button", { type: "button", class: "cp-danger cp-mini" }, "✕");
    if (slotCount(r)) {   // can't delete an activity with placed slots (the api refuses too)
      del.disabled = true;
      del.title = "Nelze smazat – aktivita má naplánované sloty. Nejprve je odeber z timeline.";
    } else {
      del.title = "Smazat aktivitu";
      del.addEventListener("click", () => deleteActivity(r, del));
    }
    return el("td", { class: "cp-actions" }, merge, del);
  }

  function activityRow(r) {
    const tr = el("tr", null,
      el("td", null, el("a", { href: withId(U.activityDetail, r.id) }, r.title)),
      r.category ? el("td", null, swatch(r.category.color), " ", r.category.label) : el("td", { class: "cp-muted" }, "—"),
      orgCell(r), progressCell(r.todos), progressCell(r.materials));
    PINNED.forEach((tag) => tr.append(tagCell(tag, r)));
    tr.append(el("td", { class: "cp-ov-slots" }, slotText(r)));
    if (mayEdit) tr.append(actionCell(r));
    return tr;
  }

  // --- filtering + sorting (client-side over ROWS) ---------------------------
  function passes(r) {
    if (filter.categoryId != null && (!r.category || r.category.id !== filter.categoryId)) return false;
    if (filter.unfinishedTodos && r.todos.total - r.todos.done <= 0) return false;
    if (filter.unfinishedMaterials && r.materials.total - r.materials.done <= 0) return false;
    if (filter.orgIds.size) {   // "jen garanti" narrows the match to garant assignments
      const ids = filter.garantsOnly ? r.garant_ids : r.org_ids;
      if (!ids.some((id) => filter.orgIds.has(id))) return false;
    }
    for (const [tagId, state] of filter.tags) {
      if (state === "has" && !hasTag(r, tagId)) return false;
      if (state === "checked" && r.tags[tagId] !== "true") return false;
      if (state === "unchecked" && (!hasTag(r, tagId) || r.tags[tagId] === "true")) return false;
    }
    return true;
  }

  // Build the active comparator once per render: parse sortKey a single time (not per comparison).
  // Each column's natural order is encoded as a numeric `primary`; sortDir flips it, title breaks ties.
  function makeSorter() {
    const dir = sortDir;
    const byTitle = (a, b) => a.title.localeCompare(b.title, "cs");
    if (!sortKey.startsWith("tag:")) return (a, b) => byTitle(a, b) * dir;
    const [, idStr, kind] = sortKey.split(":");
    const id = Number(idStr);
    const primary = kind === "check"
      ? (r) => (r.tags[id] === "true" ? 0 : 1)                       // checked first
      : (r) => (hasTag(r, id) ? -clampPct(r.tags[id]) : 1);          // progress: highest first; absent last
    return (a, b) => (primary(a) - primary(b)) * dir || byTitle(a, b);
  }

  function renderTableBody() {
    const rows = ROWS.filter(passes).sort(makeSorter());
    tbody.replaceChildren(...rows.map(activityRow));
    if (!rows.length) {
      tbody.append(el("tr", null, el("td", { colspan: String(colCount()), class: "cp-muted cp-ov-empty" },
        ROWS.length ? "Žádná aktivita neodpovídá filtru." : "Zatím žádné aktivity.")));
    }
    countLabel.textContent = "Zobrazeno " + rows.length + " z " + ROWS.length;
  }

  // --- header controls (sort + filter live in the column headers) ------------
  function setSort(key) {
    if (key === sortKey) sortDir = -sortDir; else { sortKey = key; sortDir = 1; }   // re-click reverses
    sortArrows.forEach((span, k) => { span.textContent = arrowFor(k); });
    renderTableBody();
  }

  // A header whose label is a sort toggle. Registers its arrow indicator for in-place updates.
  function sortHead(label, key, extraClass) {
    const arrow = el("span", { class: "cp-th-arrow" }, arrowFor(key));
    sortArrows.set(key, arrow);
    const btn = el("button", { type: "button", class: "cp-th-sort" }, label, arrow);
    btn.addEventListener("click", () => setSort(key));
    return el("th", { class: extraClass || null }, btn);
  }

  const onFilterChange = () => renderTableBody();

  function categoryHead() {
    const sel = el("select", { class: "cp-th-filter" });
    sel.append(el("option", { value: "" }, "Vše"));
    CATEGORIES.forEach((c) => sel.append(el("option", { value: String(c.id) }, c.label)));
    sel.value = filter.categoryId != null ? String(filter.categoryId) : "";
    sel.addEventListener("change", () => { filter.categoryId = sel.value ? Number(sel.value) : null; onFilterChange(); });
    return el("th", null, el("span", { class: "cp-th-label" }, "Kategorie"), sel);
  }

  // a header with an "jen nehotové" checkbox bound to a boolean filter field
  function unfinishedHead(label, fieldName) {
    const cb = el("input", { type: "checkbox" });
    cb.checked = filter[fieldName];
    cb.addEventListener("change", () => { filter[fieldName] = cb.checked; onFilterChange(); });
    return el("th", null, el("span", { class: "cp-th-label" }, label),
      el("label", { class: "cp-th-check", title: "Jen s nehotovými" }, cb, " jen nehotové"));
  }

  function orgsHead() {
    const btn = el("button", { type: "button", class: "cp-th-filter cp-th-dd-btn" });
    const setLabel = () => { btn.textContent = filter.orgIds.size ? "Orgové (" + filter.orgIds.size + ") ▾" : "Vše ▾"; };
    setLabel();
    const panel = el("div", { class: "cp-th-pop", hidden: true });
    panel.addEventListener("click", (e) => e.stopPropagation());   // keep clicks inside from closing the dropdown
    // "jen garanti" — restrict the org match below to garant assignments (ignore helpers)
    const gCb = el("input", { type: "checkbox" });
    gCb.checked = filter.garantsOnly;
    gCb.addEventListener("change", () => { filter.garantsOnly = gCb.checked; onFilterChange(); });
    panel.append(el("label", { class: "cp-th-pop-row cp-th-pop-opt" }, gCb, " jen garant"));
    ORGS.forEach((o) => {
      const cb = el("input", { type: "checkbox" });
      cb.checked = filter.orgIds.has(o.id);
      cb.addEventListener("change", () => {
        if (cb.checked) filter.orgIds.add(o.id); else filter.orgIds.delete(o.id);
        setLabel(); onFilterChange();
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
    return el("th", null, el("span", { class: "cp-th-label" }, "Orgové"), el("div", { class: "cp-th-dd" }, btn, panel));
  }

  // a pinned-tag header: sortable when check/progress, with a presence/state filter select
  function tagHead(tag) {
    const sel = el("select", { class: "cp-th-filter" });
    sel.append(el("option", { value: "any" }, "Vše"), el("option", { value: "has" }, "Má štítek"));
    if (tag.kind === "check")
      sel.append(el("option", { value: "checked" }, "Zaškrtnuté"), el("option", { value: "unchecked" }, "Nezaškrtnuté"));
    sel.value = filter.tags.get(tag.id) || "any";
    sel.addEventListener("change", () => {
      if (sel.value === "any") filter.tags.delete(tag.id); else filter.tags.set(tag.id, sel.value);
      onFilterChange();
    });

    let titleNode;
    if (tag.kind === "check" || tag.kind === "progress") {
      const key = "tag:" + tag.id + ":" + tag.kind;
      const arrow = el("span", { class: "cp-th-arrow" }, arrowFor(key));
      sortArrows.set(key, arrow);
      titleNode = el("button", { type: "button", class: "cp-th-sort", title: "Seřadit" }, tag.name, arrow);
      titleNode.addEventListener("click", () => setSort(key));
    } else {
      titleNode = el("span", { class: "cp-th-label" }, tag.name);
    }
    return el("th", { class: "cp-ov-tag", title: tag.name }, titleNode, sel);
  }

  function resetFilters() {
    filter.categoryId = null; filter.unfinishedTodos = false; filter.unfinishedMaterials = false;
    filter.orgIds.clear(); filter.tags.clear(); filter.garantsOnly = false;
    sortKey = "title"; sortDir = 1;
    buildShell();
  }

  // --- actions ---------------------------------------------------------------
  function deleteActivity(r, btn) {
    if (!confirm("Smazat aktivitu „" + r.title + "“?")) return;
    btn.disabled = true;
    api("DELETE", withId(U.activityItem, r.id))
      .then(() => {
        const i = ROWS.findIndex((x) => x.id === r.id);
        if (i >= 0) ROWS.splice(i, 1);
        renderTableBody();
        toast("Smazáno");
      })
      .catch((e) => { btn.disabled = false; toast(e.message, true); });
  }

  // Merge this activity INTO another (picked, fuzzy). The server moves todos/slots/needs and
  // deletes the source, so we reload rather than reconcile the table locally.
  function openMerge(r) {
    const others = ROWS.filter((x) => x.id !== r.id);
    if (!others.length) { toast("Není do čeho slučovat — v akci je jen tahle aktivita.", true); return; }
    const search = el("input", { type: "text", class: "cp-modal-search", placeholder: "Sloučit do…" });
    const list = el("div", { class: "cp-modal-list" });
    const cancel = el("button", { type: "button", class: "cp-cancel" }, "Zrušit");
    const dialog = el("div", { class: "cp-modal" },
      el("div", { class: "cp-modal-head" }, "Sloučit „" + r.title + "“ do…"),
      el("div", { class: "cp-pane" },
        el("p", { class: "cp-muted" },
          "Úkoly, sloty a materiál se přesunou do vybrané aktivity (množství stejného materiálu se sečtou). " +
          "Štítky a orgové z „" + r.title + "“ se zahodí a aktivita se smaže."),
        search, list),
      el("div", { class: "cp-modal-foot" }, cancel));
    const close = openModal(dialog);
    cancel.addEventListener("click", () => close());

    const setRows = keyList(search);
    let merging = false;   // guard against a second pick while the merge + reload is in flight
    function pick(t) {
      if (merging) return;
      if (!confirm("Sloučit „" + r.title + "“ do „" + t.title + "“?")) return;
      merging = true;
      api("POST", mergeUrl(r.id), { into: t.id })
        .then(() => { close(); toastNext("Sloučeno do „" + t.title + "“"); location.reload(); })
        .catch((e) => { merging = false; toast(e.message, true); });
    }
    function renderResults() {
      const q = search.value.trim();
      const matches = q && window.cpFuzzy ? window.cpFuzzy.filter(q, others, (x) => x.title) : others;
      const entries = matches.map((t) => ({
        el: el("button", { type: "button", class: "cp-modal-item" },
          el("span", null, t.title),
          t.category ? el("span", { class: "cp-modal-recent" }, t.category.label) : null),
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

  // --- shell -----------------------------------------------------------------
  function buildShell() {
    if (!ROWS.length) {
      mount.replaceChildren(el("p", { class: "cp-muted" }, "Zatím žádné aktivity — vytvoř je z timeline."));
      return;
    }
    sortArrows.clear();
    openPopover = null;
    const headRow = el("tr", null,
      sortHead("Název", "title"), categoryHead(), orgsHead(),
      unfinishedHead("Úkoly", "unfinishedTodos"), unfinishedHead("Materiál", "unfinishedMaterials"));
    PINNED.forEach((t) => headRow.append(tagHead(t)));
    headRow.append(el("th", null, el("span", { class: "cp-th-label" }, "Sloty")));
    if (mayEdit) headRow.append(el("th", { class: "cp-actions" }, ""));

    const reset = el("button", { type: "button", class: "cp-mini" }, "Zrušit filtry");
    reset.addEventListener("click", resetFilters);
    countLabel = el("span", { class: "cp-muted cp-ov-count" });
    const toolbar = el("div", { class: "cp-ov-toolbar" }, countLabel, reset);

    tbody = el("tbody");
    const table = el("table", { class: "cp-table cp-ov-table" }, el("thead", null, headRow), tbody);
    mount.replaceChildren(toolbar, table);
    // buildShell only runs with filters cleared (initial load or "Zrušit filtry"), so this
    // paints the full set; pin the resulting column widths so later filtered re-renders —
    // which show only the matching rows — can no longer reflow the columns.
    renderTableBody();
    freezeColumns(table, headRow);
  }

  // Freeze the current column widths into a <colgroup> + table-layout:fixed. Called after a
  // full-data render so the widths fit the widest content; subsequent filtered renders keep them.
  function freezeColumns(table, headRow) {
    const widths = [...headRow.children].map((th) => th.getBoundingClientRect().width);
    const colgroup = el("colgroup");
    widths.forEach((w) => { const c = el("col"); c.style.width = Math.round(w) + "px"; colgroup.append(c); });
    table.insertBefore(colgroup, table.firstChild);
    table.style.tableLayout = "fixed";
    table.style.width = Math.round(widths.reduce((a, b) => a + b, 0)) + "px";
  }

  // close an open org dropdown when clicking anywhere outside it
  document.addEventListener("click", () => { if (openPopover) { openPopover.hidden = true; openPopover = null; } });

  buildShell();
})();
