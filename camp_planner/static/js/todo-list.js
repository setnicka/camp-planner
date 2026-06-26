// Camp Planner — shared TODO list: a filterable, sortable table of tasks.
//
// Used by both the camp-wide TODO overview page (every activity's todos, with an activity
// column + filter/sort) and the TODO tab of the activity detail page (one activity's todos,
// activity column/filter/sort omitted). Each row shows the task (checkbox + title), its
// activity, the responsible orgs, the due date and the note, plus edit/delete actions when
// mayEdit. Filtering/sorting are driven from the column headers (inspired by the activity
// overview). Toggling done needs no confirmation; delete asks. Mutations go to /api; the
// passed `todos` array is mutated in place and onChange() fires after every change.
//
// Exposed as window.cpTodoList; load after dom.js.
"use strict";

window.cpTodoList = function (opts) {
  const { el, api, openModal, chipGroup, toast, plural, freezeColumns } = window.cpDom;
  const mount = opts.mount;
  const TODOS = opts.todos;                 // mutated in place (push/splice/assign)
  const ORGS = opts.orgs || [];             // [{id, initials, name}] — filter + edit picker
  const ACTS = opts.activities || [];       // [{id, title}] — activity filter/sort/link
  const U = opts.urls;                      // {item, create?, activityDetail?}
  const mayEdit = !!opts.mayEdit;
  const showActivity = !!opts.showActivity;
  const useHash = !!opts.useHash;
  const notesToggle = !!opts.notesToggle;   // render a "show notes" toggle (notes hidden by default)
  const resetButton = opts.resetButton !== false;   // "Zrušit filtry" button (default true)
  // optional leading hash token to keep before our filter params (e.g. the activity page's tab:
  // "todos" → #todos&done=1). Empty on pages where the whole hash is ours (#done=1).
  const hashPrefix = opts.hashPrefix || "";
  const onChange = opts.onChange || function () {};

  const withId = (tpl, id) => tpl.replace(/\d+$/, id);   // swap the trailing 0 sentinel
  const orgName = new Map(ORGS.map((o) => [o.id, o.name]));

  // filter + sort state (cleared by resetFilters → buildShell, which rebuilds the controls)
  // noOrg = match todos with no assigned org (OR-combined with any selected orgIds)
  const filter = { done: null, activityId: null, orgIds: new Set(), noOrg: false };   // done: null|true|false
  let sortKey = "activity";       // "activity" (default) | "due"
  let sortDir = 1;                // 1 = natural order, -1 = reversed (second click)
  let showNotes = !notesToggle;   // with the toggle (overview) notes start hidden; otherwise always shown
  let tbody, countLabel;
  const sortArrows = new Map();   // sortKey -> direction indicator span, updated in place
  const arrowFor = (key) => (key === sortKey ? (sortDir === 1 ? " ▾" : " ▴") : "");
  let openPopover = null;         // the currently-open org dropdown panel (closed on outside click)
  let orgSetLabel = null;   // org-filter button's label setter (for sizing the freeze to the widest label)
  // columns: task + orgs + due (3), plus activity and/or actions. The note is NOT a column —
  // it renders on its own full-width second row beneath the task.
  const colCount = () => 3 + (showActivity ? 1 : 0) + (mayEdit ? 1 : 0);

  // --- due indicator ---------------------------------------------------------
  // Completed → the plain date; otherwise the whole-day delta from today: remaining days in
  // green, overdue days in red, due-today neutral.
  function dueBadge(t) {
    if (!t.due_date) return el("span", { class: "cp-muted" }, "—");
    if (t.is_done) return el("span", { class: "cp-muted cp-todo-due" }, t.due_date);
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const [y, m, d] = t.due_date.split("-").map(Number);
    const days = Math.round((new Date(y, m - 1, d) - today) / 86400000);
    let text, cls;
    if (days > 0) { text = "zbývá " + days + " " + plural(days, "den", "dny", "dní"); cls = "cp-due-ok"; }
    else if (days === 0) { text = "dnes"; cls = "cp-due-ok"; }
    else { const n = -days; text = "po termínu " + n + " " + plural(n, "den", "dny", "dní"); cls = "cp-due-late"; }
    return el("span", { class: "cp-todo-due " + cls, title: t.due_date }, text);
  }

  // --- row -------------------------------------------------------------------
  function taskCell(t) {
    const cb = el("input", { type: "checkbox" });
    cb.checked = t.is_done;
    cb.disabled = !mayEdit;
    if (mayEdit) cb.addEventListener("change", async () => {   // toggle done — no confirmation
      try { const j = await api("PATCH", withId(U.item, t.id), { is_done: cb.checked }); Object.assign(t, j.todo); refresh(); }
      catch (e) { cb.checked = !cb.checked; toast(e.message, true); }
    });
    const title = el("span", { class: "cp-todo-title" + (t.is_done ? " done" : "") }, t.title);
    return el("td", { class: "cp-todo-task" }, el("label", { class: "cp-todo-task-lbl" }, cb, title));
  }

  function orgsCell(t) {
    if (!t.orgs.length) return el("td", { class: "cp-muted" }, "—");
    const td = el("td", { class: "cp-todo-orgs" });
    t.orgs.forEach((o, i) => {
      if (i) td.append(", ");
      td.append(el("span", { title: orgName.get(o.org_id) || "" }, o.initials));
    });
    return td;
  }

  function actionsCell(t) {
    const edit = el("button", { type: "button", class: "cp-mini", title: "Upravit" }, "✎");
    edit.addEventListener("click", () => openForm(t));
    const del = el("button", { type: "button", class: "cp-danger cp-mini", title: "Smazat" }, "✕");
    del.addEventListener("click", async () => {
      if (!confirm("Smazat úkol „" + t.title + "“?")) return;
      del.disabled = true;
      try {
        await api("DELETE", withId(U.item, t.id));
        const i = TODOS.findIndex((x) => x.id === t.id);
        if (i >= 0) TODOS.splice(i, 1);
        refresh(); toast("Smazáno");
      } catch (e) { del.disabled = false; toast(e.message, true); }
    });
    return el("td", { class: "cp-actions" }, edit, del);
  }

  // Returns the task row, plus a full-width second row carrying the note when present (a
   // fragment, so renderTableBody can spread both into the tbody).
  function todoRow(t) {
    const withNote = t.note && showNotes;   // a note row follows only then → drop this row's border only then
    const tr = el("tr", { class: withNote ? "cp-todo-has-note" : null }, taskCell(t));
    if (showActivity) {
      const link = U.activityDetail
        ? el("a", { href: withId(U.activityDetail, t.activity_id) + "#todos" }, t.activity_title)
        : el("span", null, t.activity_title);
      tr.append(el("td", null, link));
    }
    tr.append(orgsCell(t), el("td", { class: "cp-todo-due-cell" }, dueBadge(t)));
    if (mayEdit) tr.append(actionsCell(t));
    if (!withNote) return tr;
    const noteTr = el("tr", { class: "cp-todo-note-row" },
      el("td", { colspan: String(colCount()) }, el("div", { class: "cp-todo-note" }, t.note)));
    const frag = document.createDocumentFragment();
    frag.append(tr, noteTr);
    return frag;
  }

  // --- filter/sort state <-> URL hash ----------------------------------------
  function stateToQuery() {
    const p = new URLSearchParams();
    if (filter.done !== null) p.set("done", filter.done ? "1" : "0");
    if (showActivity && filter.activityId != null) p.set("act", filter.activityId);
    if (filter.noOrg) p.set("noorg", "1");
    filter.orgIds.forEach((id) => p.append("org", id));
    if (sortKey !== "activity" || sortDir !== 1) { p.set("sort", sortKey); if (sortDir !== 1) p.set("dir", "-1"); }
    return p.toString();
  }
  function writeHash() {
    if (!useHash) return;
    const q = stateToQuery();
    const h = hashPrefix ? (q ? hashPrefix + "&" + q : hashPrefix) : q;
    history.replaceState(null, "", h ? "#" + h : location.pathname + location.search);
  }
  // The filter portion of the hash. On a tabbed host (hashPrefix set) the first &-segment is the
  // tab token — drop it whatever its value, so our filters apply even when another tab leads the
  // hash (e.g. reloading on #materials&done=1). Without a prefix the whole hash is ours.
  function hashQuery() {
    const raw = location.hash.slice(1);
    return new URLSearchParams(hashPrefix ? raw.split("&").slice(1).join("&") : raw);
  }
  // Mutate filter + sort from the current hash, validating every value against the real
  // activities/orgs so a stale or hand-edited link can't wedge the view into an impossible state.
  function applyHashToState() {
    if (!useHash) return;
    const p = hashQuery();
    const done = p.get("done");
    filter.done = done === "1" ? true : done === "0" ? false : null;
    const actId = Number(p.get("act"));
    filter.activityId = showActivity && ACTS.some((a) => a.id === actId) ? actId : null;
    filter.orgIds = new Set(p.getAll("org").map(Number).filter((id) => ORGS.some((o) => o.id === id)));
    filter.noOrg = p.get("noorg") === "1";
    const sort = p.get("sort");
    sortKey = (sort === "due" || (sort === "activity" && showActivity)) ? sort : "activity";
    sortDir = p.get("dir") === "-1" ? -1 : 1;
  }

  // --- filtering + sorting (client-side over TODOS) --------------------------
  function passes(t) {
    if (filter.done !== null && t.is_done !== filter.done) return false;
    if (showActivity && filter.activityId != null && t.activity_id !== filter.activityId) return false;
    if (filter.orgIds.size || filter.noOrg) {   // OR: matches a selected org, or (noOrg) is unassigned
      const ok = t.orgs.some((o) => filter.orgIds.has(o.org_id)) || (filter.noOrg && !t.orgs.length);
      if (!ok) return false;
    }
    return true;
  }

  // Build the active comparator once per render. Each column's natural order; sortDir flips it,
  // id breaks ties (preserves creation order, the natural default when no activity column).
  function makeSorter() {
    const dir = sortDir;
    const byId = (a, b) => a.id - b.id;
    if (sortKey === "due") {
      return (a, b) => {
        if (!a.due_date && !b.due_date) return byId(a, b);
        if (!a.due_date) return 1;            // tasks without a due date sort last
        if (!b.due_date) return -1;
        return (a.due_date < b.due_date ? -1 : a.due_date > b.due_date ? 1 : 0) * dir || byId(a, b);
      };
    }
    return (a, b) => (a.activity_title || "").localeCompare(b.activity_title || "", "cs") * dir || byId(a, b);
  }

  function renderTableBody() {
    const rows = TODOS.filter(passes).sort(makeSorter());
    tbody.replaceChildren(...rows.map(todoRow));
    if (!rows.length) {
      tbody.append(el("tr", null, el("td", { colspan: String(colCount()), class: "cp-muted cp-todo-empty" },
        TODOS.length ? "Žádný úkol neodpovídá filtru." : "Žádné úkoly.")));
    }
    countLabel.textContent = "Zobrazeno " + rows.length + " z " + TODOS.length;
  }

  // re-render the body and notify the host (counts live outside this component)
  function refresh() { renderTableBody(); onChange(); }

  // --- header controls (sort + filter live in the column headers) ------------
  function setSort(key) {
    if (key === sortKey) sortDir = -sortDir; else { sortKey = key; sortDir = 1; }   // re-click reverses
    sortArrows.forEach((span, k) => { span.textContent = arrowFor(k); });
    writeHash();
    renderTableBody();
  }
  function sortLabel(label, key) {
    const arrow = el("span", { class: "cp-th-arrow" }, arrowFor(key));
    sortArrows.set(key, arrow);
    const btn = el("button", { type: "button", class: "cp-th-sort", title: "Seřadit" }, label, arrow);
    btn.addEventListener("click", () => setSort(key));
    return btn;
  }

  const onFilterChange = () => { writeHash(); renderTableBody(); };

  function statusHead() {
    const sel = el("select", { class: "cp-th-filter" });
    sel.append(el("option", { value: "any" }, "Vše"),
      el("option", { value: "0" }, "Nehotové"), el("option", { value: "1" }, "Hotové"));
    sel.value = filter.done === null ? "any" : filter.done ? "1" : "0";
    sel.addEventListener("change", () => {
      filter.done = sel.value === "any" ? null : sel.value === "1";
      onFilterChange();
    });
    return el("th", { class: "cp-todo-task" }, el("span", { class: "cp-th-label" }, "Úkol"), sel);
  }

  function activityHead() {
    const sel = el("select", { class: "cp-th-filter" });
    sel.append(el("option", { value: "" }, "Vše"));
    ACTS.forEach((a) => sel.append(el("option", { value: String(a.id) }, a.title)));
    sel.value = filter.activityId != null ? String(filter.activityId) : "";
    sel.addEventListener("change", () => { filter.activityId = sel.value ? Number(sel.value) : null; onFilterChange(); });
    return el("th", null, sortLabel("Aktivita", "activity"), sel);
  }

  function orgsHead() {
    const btn = el("button", { type: "button", class: "cp-th-filter cp-th-dd-btn" });
    const count = () => filter.orgIds.size + (filter.noOrg ? 1 : 0);
    // setLabel(n) renders the button for a given count; default = the live count. Passing an
    // explicit n lets the freeze size the column to the widest label without duplicating the format.
    const setLabel = (n = count()) => { btn.textContent = n ? "Orgové (" + n + ") ▾" : "Vše ▾"; };
    setLabel();
    orgSetLabel = setLabel;   // exposed so freeze can size to the widest label
    const panel = el("div", { class: "cp-th-pop", hidden: true });
    panel.addEventListener("click", (e) => e.stopPropagation());   // keep clicks inside from closing the dropdown
    // "bez orgů" — match unassigned todos (OR-combined with any orgs ticked below)
    const noCb = el("input", { type: "checkbox" });
    noCb.checked = filter.noOrg;
    noCb.addEventListener("change", () => { filter.noOrg = noCb.checked; setLabel(); onFilterChange(); });
    panel.append(el("label", { class: "cp-th-pop-row cp-th-pop-opt" }, noCb, " bez orgů"));
    if (!ORGS.length) panel.append(el("div", { class: "cp-muted" }, "Žádní orgové."));
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

  function resetFilters() {
    filter.done = null; filter.activityId = null; filter.orgIds.clear(); filter.noOrg = false;
    sortKey = "activity"; sortDir = 1;
    writeHash();
    buildShell();
  }

  // --- add/edit form ---------------------------------------------------------
  // `t` null = create (only when U.create is set, i.e. the activity tab), otherwise edit.
  function openForm(t) {
    const seed = t || {};
    const title = el("input", { type: "text", class: "cp-modal-name", value: seed.title || "" });
    const note = el("textarea", { class: "cp-act-textarea", rows: 3 });
    note.value = seed.note || "";
    const due = el("input", { type: "date" });
    if (seed.due_date) due.value = seed.due_date;
    const group = chipGroup(ORGS.map((o) => [o.id, el("b", null, o.initials), " " + o.name]),
      { multi: true, selected: (seed.orgs || []).map((o) => o.org_id) });
    if (!ORGS.length) group.node.append(el("div", { class: "cp-muted" }, "Žádní orgové — přidejte je v nastavení akce."));
    const cancel = el("button", { type: "button", class: "cp-cancel" }, "Zrušit");
    const ok = el("button", { type: "button", class: "cp-primary" }, t ? "Uložit" : "Přidat");
    const dialog = el("div", { class: "cp-modal cp-modal-wide" },
      el("div", { class: "cp-modal-head" }, t ? "Upravit úkol" : "Nový úkol"),
      el("div", { class: "cp-pane" },
        el("label", { class: "cp-field-label" }, "Název"), title,
        el("label", { class: "cp-field-label" }, "Poznámka"), note,
        el("label", { class: "cp-field-label" }, "Termín"), due,
        el("label", { class: "cp-field-label" }, "Orgové"), group.node),
      el("div", { class: "cp-modal-foot" }, cancel, ok));
    const close = openModal(dialog);
    cancel.addEventListener("click", close);
    ok.addEventListener("click", async () => {
      const v = title.value.trim();
      if (!v) { title.focus(); return; }
      const body = { title: v, note: note.value || null, due_date: due.value || null, org_ids: group.get() };
      ok.disabled = true;
      try {
        if (t) { const j = await api("PATCH", withId(U.item, t.id), body); Object.assign(t, j.todo); }
        else { const j = await api("POST", U.create, body); TODOS.push(j.todo); }
        close(); refresh(); toast("Uloženo");
      } catch (e) { ok.disabled = false; toast(e.message, true); }
    });
    title.focus();
  }

  // --- shell -----------------------------------------------------------------
  function buildShell() {
    sortArrows.clear();
    openPopover = null;
    const headRow = el("tr", null, statusHead());
    if (showActivity) headRow.append(activityHead());
    headRow.append(orgsHead(), el("th", null, sortLabel("Termín", "due")));
    if (mayEdit) headRow.append(el("th", { class: "cp-actions" }, ""));

    countLabel = el("span", { class: "cp-muted cp-todo-count" });
    const toolbar = el("div", { class: "cp-todo-toolbar" }, countLabel);   // countLabel margin-left:auto → hugs right
    if (resetButton) {
      const reset = el("button", { type: "button", class: "cp-mini" }, "Zrušit filtry");
      reset.addEventListener("click", resetFilters);
      toolbar.append(reset);
    }
    // optional "show notes" toggle, on the right of the toolbar (notes hidden by default)
    if (notesToggle) {
      const cb = el("input", { type: "checkbox" });
      cb.checked = showNotes;
      cb.addEventListener("change", () => { showNotes = cb.checked; renderTableBody(); });
      toolbar.append(el("label", { class: "cp-todo-notes-toggle" }, cb, " Zobrazit poznámky"));
    }

    tbody = el("tbody");
    const table = el("table", { class: "cp-table cp-todo-table" }, el("thead", null, headRow), tbody);
    const children = [toolbar, table];
    if (U.create && mayEdit) {   // "+ Přidat úkol" sits below the table (activity TODO tab)
      const add = el("button", { type: "button", class: "cp-add cp-todo-add" }, "+ Přidat úkol");
      add.addEventListener("click", () => openForm(null));
      children.push(add);
    }
    mount.replaceChildren(...children);
    // Paint the full set first so the frozen column widths fit the widest content, then apply
    // any active filter. Pinning the widths up front stops later filtered re-renders — which
    // show only the matching rows — from reflowing (jumping) the columns.
    tbody.replaceChildren(...TODOS.map(todoRow));
    // Size the org-filter header to its WIDEST possible label before measuring, so the frozen
    // widths don't depend on how many orgs are currently selected — otherwise reloading with an
    // org filter active (button reads "Orgové (N) ▾", wider than "Vše ▾") would freeze different
    // widths than filtering live, and the columns would jump on reload.
    if (orgSetLabel) orgSetLabel(ORGS.length + 1);   // widest label → frozen widths don't depend on the filter
    freezeColumns(table, headRow);
    if (orgSetLabel) orgSetLabel();                  // restore the real (live-count) label
    renderTableBody();
  }

  // close an open org dropdown when clicking anywhere outside it
  document.addEventListener("click", () => { if (openPopover) { openPopover.hidden = true; openPopover = null; } });
  // External links / back button (overview page only): re-read the hash and rebuild.
  if (useHash) window.addEventListener("hashchange", () => { applyHashToState(); buildShell(); });

  applyHashToState();   // restore filters/sort from the URL before the first paint
  buildShell();

  return { refresh, rebuild: buildShell };
};
