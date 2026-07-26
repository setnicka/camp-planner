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

  const { el, api, withId, mergeUrl, swatch, mergePicker, orgFilterHead, toast, plural, freezeColumns } = window.cpDom;
  const DATA = JSON.parse(dataEl.textContent);
  const U = DATA.urls;
  const mayEdit = DATA.may_edit;
  const ROWS = DATA.activities;             // mutated in place (delete splices; merge reloads)
  const CATEGORIES = DATA.categories;
  const ORGS = DATA.orgs;
  const PINNED = DATA.pinned_tags;          // [{id, name, kind}] — table columns + filter/sort
  const CAMP = DATA.camp;                   // {start_date, length_days, window_start_min} — chrono day math

  const clampPct = (v) => Math.max(0, Math.min(100, parseInt(v, 10) || 0));
  const slotCount = (r) => r.slots.length;                                    // any placed slot (any role)
  const mainSlots = (r) => r.slots.filter((s) => s.role === "main");          // time-ordered (server-sorted)
  const hasTag = (r, id) => Object.prototype.hasOwnProperty.call(r.tags, id);   // key present = tag applies
  // Column count is the same in both modes: chronological swaps the trailing "Sloty" count
  // column for a leading "Čas" column.
  const colCount = () => 6 + PINNED.length + (mayEdit ? 1 : 0);

  // --- camp-day math (chronological mode) ------------------------------------
  // Group main slots into camp days the same way the timeline does: a day is a 24h window
  // anchored at window_start_min (not midnight), so a night program past midnight stays on
  // its day's row. Times are naive wall-clock — no timezone conversion (see models/slot.py).
  const DAY_MIN = 1440;
  const CZ_WEEKDAYS = ["Neděle", "Pondělí", "Úterý", "Středa", "Čtvrtek", "Pátek", "Sobota"]; // getUTCDay: 0=Ne
  const [SD_Y, SD_M, SD_D] = CAMP.start_date.split("-").map(Number);
  const ORIGIN = Date.UTC(SD_Y, SD_M - 1, SD_D);                             // midnight of start_date, as UTC ms
  // Camp-day index (0..length_days-1) for a naive ISO datetime. Parse the parts — never
  // `new Date(str)`, which would apply the browser timezone — into minutes from the camp origin,
  // then bucket by the 24h window anchored at window_start_min. Clamped to the span so every main
  // slot lands on a real divider (matches the segment clamping in services/timeline.py).
  function dayOf(iso) {
    const [datePart, timePart] = iso.split("T");
    const [y, mo, d] = datePart.split("-").map(Number);
    const [hh, mm] = timePart.split(":").map(Number);
    const abs = Math.round((Date.UTC(y, mo - 1, d) - ORIGIN) / 86400000) * DAY_MIN + hh * 60 + mm;
    return Math.max(0, Math.min(CAMP.length_days - 1, Math.floor((abs - CAMP.window_start_min) / DAY_MIN)));
  }
  // Full-width day-divider label, e.g. "Čtvrtek 9. 7." — parse as UTC so the weekday can't roll.
  function dayLabel(i) {
    const dt = new Date(ORIGIN + i * 86400000);
    return CZ_WEEKDAYS[dt.getUTCDay()] + " " + dt.getUTCDate() + ". " + (dt.getUTCMonth() + 1) + ".";
  }
  const fmtTime = (iso) => { const [h, m] = iso.split("T")[1].split(":"); return Number(h) + ":" + m; };
  const slotRange = (s) => fmtTime(s.start_at) + "–" + fmtTime(s.end_at);

  // filter + sort state (cleared by resetFilters → buildShell, which rebuilds the controls)
  const filter = { categoryId: null, unfinishedTodos: false, unfinishedMaterials: false,
                   orgIds: new Set(), garantsOnly: false, tags: new Map() };   // tags: tagId -> "has"|"checked"|"unchecked"
  let sortKey = "title";
  let sortDir = 1;                // 1 = the column's natural order, -1 = reversed (second click)
  let chrono = false;            // chronological mode: rows = one per main slot, grouped by camp day
  let tbody, countLabel;
  const sortArrows = new Map();   // sortKey -> the direction indicator span, updated in place on sort change
  const arrowFor = (key) => (!chrono && key === sortKey ? (sortDir === 1 ? " ▾" : " ▴") : "");

  // --- cell renderers --------------------------------------------------------
  function slotText(r) {
    if (!r.slots.length) return "—";
    const n = { main: 0, prep: 0, cleanup: 0 };
    r.slots.forEach((s) => { n[s.role]++; });                                 // count by role from the slot list
    let s = n.main + " " + plural(n.main, "slot", "sloty", "slotů");
    const extra = [];
    if (n.prep) extra.push("+" + n.prep + " " + plural(n.prep, "příprava", "přípravy", "příprav"));
    if (n.cleanup) extra.push("+" + n.cleanup + " " + plural(n.cleanup, "úklid", "úklidy", "úklidů"));
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

  // Cells shared by both row layouts, from category through the actions column.
  function commonCells(r) {
    const cells = [
      r.category ? el("td", null, swatch(r.category.color), " ", r.category.label) : el("td", { class: "cp-muted" }, "—"),
      orgCell(r), progressCell(r.todos), progressCell(r.materials)];
    PINNED.forEach((tag) => cells.push(tagCell(tag, r)));
    if (!chrono) cells.push(el("td", { class: "cp-ov-slots" }, slotText(r)));   // "Čas" replaces it in chrono mode
    if (mayEdit) cells.push(actionCell(r));
    return cells;
  }

  function activityRow(r) {
    return el("tr", null,
      el("td", null, el("a", { href: withId(U.activityDetail, r.id) }, r.title)), ...commonCells(r));
  }

  // One chronological row: a leading time cell + a slot-aware title (override_name primary, with
  // the activity title in muted parens after it), then the shared activity cells. `slot` is null
  // for the no-main-slot bottom bucket.
  function chronoRow(r, slot) {
    const link = el("a", { href: withId(U.activityDetail, r.id) }, (slot && slot.override_name) || r.title);
    const title = el("td", null, link);
    if (slot && slot.override_name) title.append(" ", el("span", { class: "cp-ov-act" }, "(" + r.title + ")"));
    return el("tr", null,
      el("td", { class: "cp-ov-time" }, slot ? slotRange(slot) : "—"), title, ...commonCells(r));
  }

  const dividerRow = (label, cls) =>
    el("tr", { class: "cp-ov-day" + (cls || "") },
      el("td", { class: "cp-ov-day-cell", colspan: String(colCount()) }, label));
  const emptyDayRow = () =>
    el("tr", { class: "cp-ov-day-empty" },
      el("td", { class: "cp-muted", colspan: String(colCount()) }, "— žádné aktivity —"));

  // --- filter/sort state <-> URL hash ----------------------------------------
  // Persist the whole filter + sort state in the URL hash (as a query string) so the view is
  // shareable and survives reload. Written with replaceState (no scroll-jump, no history spam);
  // read back on load and on hashchange (external links / back button). Default state → no hash.
  function sortKeyValid(key) {
    if (key === "title") return "title";
    const m = /^tag:(\d+):(check|progress)$/.exec(key || "");
    const tag = m && PINNED.find((t) => t.id === Number(m[1]));
    return tag && tag.kind === m[2] ? key : null;
  }
  function stateToHash() {
    const p = new URLSearchParams();
    if (filter.categoryId != null) p.set("cat", filter.categoryId);
    if (filter.unfinishedTodos) p.set("todos", "1");
    if (filter.unfinishedMaterials) p.set("mat", "1");
    filter.orgIds.forEach((id) => p.append("org", id));
    if (filter.garantsOnly) p.set("garants", "1");
    for (const [id, state] of filter.tags) p.set("tag" + id, state);
    if (chrono) p.set("chrono", "1");
    // Persist the column sort even while chronological — so switching back (incl. after a reload)
    // restores it rather than snapping to the default title order.
    if (sortKey !== "title" || sortDir !== 1) { p.set("sort", sortKey); if (sortDir !== 1) p.set("dir", "-1"); }
    return p.toString();
  }
  function writeHash() {
    const s = stateToHash();
    history.replaceState(null, "", s ? "#" + s : location.pathname + location.search);
  }
  // Mutate filter + sort state from the current hash, validating every value against the real
  // categories/orgs/tags so a stale or hand-edited link can't wedge the view into an impossible state.
  function applyHashToState() {
    const p = new URLSearchParams(location.hash.slice(1));
    const catId = Number(p.get("cat"));
    filter.categoryId = CATEGORIES.some((c) => c.id === catId) ? catId : null;
    filter.unfinishedTodos = p.get("todos") === "1";
    filter.unfinishedMaterials = p.get("mat") === "1";
    filter.orgIds = new Set(p.getAll("org").map(Number).filter((id) => ORGS.some((o) => o.id === id)));
    filter.garantsOnly = p.get("garants") === "1";
    filter.tags = new Map();
    PINNED.forEach((t) => {
      const v = p.get("tag" + t.id);
      if (v === "has" || ((v === "checked" || v === "unchecked") && t.kind === "check")) filter.tags.set(t.id, v);
    });
    chrono = p.get("chrono") === "1";
    const validSort = sortKeyValid(p.get("sort"));   // kept independent of chrono, so it survives a switch back
    sortKey = validSort || "title";
    sortDir = validSort && p.get("dir") === "-1" ? -1 : 1;
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
    if (chrono) return renderChronoBody();
    const rows = ROWS.filter(passes).sort(makeSorter());
    tbody.replaceChildren(...rows.map(activityRow));
    if (!rows.length) {
      tbody.append(el("tr", null, el("td", { colspan: String(colCount()), class: "cp-muted cp-ov-empty" },
        ROWS.length ? "Žádná aktivita neodpovídá filtru." : "Zatím žádné aktivity.")));
    }
    countLabel.textContent = "Zobrazeno " + rows.length + " z " + ROWS.length;
  }

  // The chronological rows an activity yields: one {r, slot} per main slot, or a single slot-less
  // entry (the "Bez hlavního slotu" bucket) when it has none. One place so render + measurement agree.
  const chronoEntries = (r) => {
    const main = mainSlots(r);
    return main.length ? main.map((slot) => ({ r, slot })) : [{ r, slot: null }];
  };

  // Chronological body: expand each passing activity into one row per main slot, grouped into
  // camp-day sections (every camp day gets a divider; an empty day gets a placeholder row).
  // Activities with no main slot fall into a trailing "Bez hlavního slotu" section, shown only
  // when non-empty. Every passing activity yields ≥1 row, so its count == the filtered length.
  function renderChronoBody() {
    const passing = ROWS.filter(passes);
    const days = Array.from({ length: CAMP.length_days }, () => []);
    const noMain = [];
    passing.flatMap(chronoEntries).forEach((e) => {
      if (e.slot) days[dayOf(e.slot.start_at)].push(e); else noMain.push(e.r);
    });
    const byStart = (a, b) =>
      a.slot.start_at.localeCompare(b.slot.start_at) || a.r.title.localeCompare(b.r.title, "cs");
    const frag = [];
    days.forEach((list, i) => {
      frag.push(dividerRow(dayLabel(i)));
      if (!list.length) { frag.push(emptyDayRow()); return; }
      list.sort(byStart).forEach((e) => frag.push(chronoRow(e.r, e.slot)));
    });
    noMain.sort((a, b) => a.title.localeCompare(b.title, "cs"));
    if (noMain.length) {
      frag.push(dividerRow("Bez hlavního slotu", " cp-ov-day-nomain"));
      noMain.forEach((r) => frag.push(chronoRow(r, null)));
    }
    tbody.replaceChildren(...frag);
    countLabel.textContent = "Zobrazeno " + passing.length + " z " + ROWS.length;
  }

  // Unfiltered rows for the up-front width measurement (see buildShell): every activity in
  // column mode, every main slot (plus no-main activities) in chronological mode.
  function measureRows() {
    return chrono
      ? ROWS.flatMap(chronoEntries).map((e) => chronoRow(e.r, e.slot))
      : ROWS.map(activityRow);
  }

  // --- header controls (sort + filter live in the column headers) ------------
  function setSort(key) {
    if (chrono) return;   // chronological mode owns the ordering — column sorting is disabled
    if (key === sortKey) sortDir = -sortDir; else { sortKey = key; sortDir = 1; }   // re-click reverses
    sortArrows.forEach((span, k) => { span.textContent = arrowFor(k); });
    writeHash();
    renderTableBody();
  }

  // A header whose label is a sort toggle. Registers its arrow indicator for in-place updates.
  // In chronological mode the header keeps its normal look but no longer sorts (setSort no-ops,
  // and arrowFor drops the arrow since no column owns the order).
  function sortHead(label, key, extraClass) {
    const arrow = el("span", { class: "cp-th-arrow" }, arrowFor(key));
    sortArrows.set(key, arrow);
    const btn = el("button", { type: "button", class: "cp-th-sort" }, label, arrow);
    btn.addEventListener("click", () => setSort(key));
    return el("th", { class: extraClass || null }, btn);
  }

  const onFilterChange = () => { writeHash(); renderTableBody(); };

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
    // "jen garant" — restrict the org match to garant assignments (ignore helpers)
    return orgFilterHead({
      orgs: ORGS, selected: filter.orgIds, onChange: onFilterChange,
      extra: { label: "jen garant", checked: filter.garantsOnly, set: (v) => { filter.garantsOnly = v; } },
    }).th;
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

  // "Zrušit filtry" clears only the filters; the sort mode (a column or chronological) is left
  // as-is — the segmented control is the only way in/out of chronological mode.
  function resetFilters() {
    filter.categoryId = null; filter.unfinishedTodos = false; filter.unfinishedMaterials = false;
    filter.orgIds.clear(); filter.tags.clear(); filter.garantsOnly = false;
    writeHash();
    buildShell();
  }

  // Switch between column-sort and chronological mode (segmented control). Rebuilds the shell
  // because the column set (leading "Čas" column) and header affordances differ between modes.
  function setChrono(on) {
    if (on === chrono) return;
    chrono = on;
    writeHash();
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
    mergePicker({
      title: "Sloučit „" + r.title + "“ do…",
      hint: "Úkoly, sloty a materiál se přesunou do vybrané aktivity (množství stejného materiálu " +
        "se sečtou). Štítky a orgové z „" + r.title + "“ se zahodí a aktivita se smaže.",
      items: others, labelOf: (t) => t.title, metaOf: (t) => t.category && t.category.label,
      url: mergeUrl(U.activityMerge, r.id),
      confirmText: (t) => "Sloučit „" + r.title + "“ do „" + t.title + "“?",
      successText: (t) => "Sloučeno do „" + t.title + "“",
    });
  }

  // --- shell -----------------------------------------------------------------
  function buildShell() {
    if (!ROWS.length) {
      mount.replaceChildren(el("p", { class: "cp-muted" }, "Zatím žádné aktivity — vytvoř je z timeline."));
      return;
    }
    sortArrows.clear();
    const headRow = el("tr", null,
      sortHead("Název", "title"), categoryHead(), orgsHead(),
      unfinishedHead("Úkoly", "unfinishedTodos"), unfinishedHead("Materiál", "unfinishedMaterials"));
    PINNED.forEach((t) => headRow.append(tagHead(t)));
    if (!chrono) headRow.append(el("th", null, el("span", { class: "cp-th-label" }, "Sloty")));   // "Čas" replaces it
    if (mayEdit) headRow.append(el("th", { class: "cp-actions" }, ""));
    // Chronological mode prepends a time column (the leading ordering key).
    if (chrono) headRow.prepend(el("th", { class: "cp-ov-time" }, el("span", { class: "cp-th-label" }, "Čas")));

    // Segmented sort-mode control: column-sorting vs chronological (day-grouped) mode.
    const segBtn = (label, mode) => {
      const b = el("button", { type: "button", class: "cp-seg-btn" + (chrono === mode ? " cp-seg-active" : "") }, label);
      b.addEventListener("click", () => setChrono(mode));
      return b;
    };
    const seg = el("div", { class: "cp-seg", role: "group" }, segBtn("Tabulka všech", false), segBtn("Chronologicky", true));

    const reset = el("button", { type: "button", class: "cp-mini" }, "Zrušit filtry");
    reset.addEventListener("click", resetFilters);
    countLabel = el("span", { class: "cp-muted cp-ov-count" });
    const toolbar = el("div", { class: "cp-ov-toolbar" }, seg, countLabel, reset);

    tbody = el("tbody");
    const table = el("table", { class: "cp-table cp-ov-table" }, el("thead", null, headRow), tbody);
    mount.replaceChildren(toolbar, table);
    // Paint the full (unfiltered) set first so the frozen column widths fit the widest content,
    // then apply any active filter. Pinning the widths up front stops later filtered re-renders —
    // which show only the matching rows — from reflowing the columns. In chronological mode the
    // full set is every main slot expanded into its own row.
    tbody.replaceChildren(...measureRows());
    freezeColumns(table, headRow);
    renderTableBody();
  }

  // External links / back button: re-read the hash and rebuild (our own writeHash uses
  // replaceState, which doesn't fire hashchange, so this can't loop).
  window.addEventListener("hashchange", () => { applyHashToState(); buildShell(); });

  applyHashToState();   // restore filters/sort from the URL before the first paint
  buildShell();
})();
