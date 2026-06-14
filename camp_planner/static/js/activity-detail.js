// Camp Planner — activity detail page (Phase 3).
//
// Renders one activity from the JSON the server inlined in #cp-activity-data and edits
// it in place via the /api endpoints — no reloads. Layout: title, a header block
// (category / orgs / tags-as-chips), then tabs (description / todos / materials). Edit
// affordances appear only when data.may_edit; the api re-checks server-side.
"use strict";

(function () {
  const mount = document.getElementById("cp-activity");
  const dataEl = document.getElementById("cp-activity-data");
  if (!mount || !dataEl) return;

  const { el, api, swatch, openModal, chipGroup, keyList, toast, plural, tabHash } = window.cpDom;
  // html:false escapes raw HTML in the source, so a rendered description can't inject markup.
  const md = window.markdownit({ html: false, linkify: true, breaks: true });
  const DATA = JSON.parse(dataEl.textContent);
  const U = DATA.urls;
  const mayEdit = DATA.may_edit;
  const A = DATA.activity;                  // current state; refreshed in place after saves
  const catById = Object.fromEntries(DATA.categories.map((c) => [c.id, c]));
  // open on the tab named in the URL hash (#todos / #materials), e.g. when arriving from
  // the materials overview; defaults to the description tab.
  const TAB = tabHash(["description", "todos", "materials"]);
  let activeTab = TAB.initial || "description";
  let descEdit = null;                      // {cm, fit, dirty} while the description editor is open
  let titleHost, headerHost, tabbarHost;    // stable region nodes, assigned once in buildShell()
  const panes = {};                         // { description, todos, materials } — built once, shown/hidden by tab

  // --- small helpers ---------------------------------------------------------
  const withId = (tpl, id) => tpl.replace(/\d+$/, id);  // swap the trailing 0 sentinel for a real id
  const clampPct = (v) => Math.max(0, Math.min(100, parseInt(v, 10) || 0));

  // Run a modal's submit: disable its button while it works; on failure re-enable and
  // toast the error. `fn` owns the success path (api call, state update, close(), toast).
  async function submit(btn, fn) {
    btn.disabled = true;
    try { await fn(); }
    catch (e) { btn.disabled = false; toast(e.message, true); }
  }

  // --- slots (header chips → timeline, this activity pre-filtered) ------------
  let slotsExpanded = false;   // when >3 slots, collapse to the first 3 until toggled open
  const SLOT_ROLE = { main: "Hlavní slot", prep: "Příprava", cleanup: "Úklid" };
  const CZ_WD = ["ne", "po", "út", "st", "čt", "pá", "so"];   // by getDay(): 0 = Sunday
  const hhmm = (iso) => iso.split("T")[1].slice(0, 5);        // naive "…T19:00:00" → "19:00"

  // A slot as a tag-style chip: role name on the left, day + time range on the right.
  // The whole chip links to the timeline with this activity preselected in the filter.
  function slotChip(s) {
    const [y, mo, d] = s.start_at.split("T")[0].split("-").map(Number);
    const day = `${CZ_WD[new Date(y, mo - 1, d).getDay()]} ${d}.${mo}`;
    // navigable segment: role | day+time | attendees → timeline with this activity pre-filtered
    const main = el("a", { class: "cp-slot-main", href: U.timeline + "#filter=activity:" + A.id, title: "Zobrazit na timeline" },
      el("span", { class: "cp-tagchip-name" }, SLOT_ROLE[s.role] || s.role),
      el("span", { class: "cp-tagchip-text" },
        el("span", { class: "cp-slot-day" }, day), " " + hhmm(s.start_at) + " – " + hhmm(s.end_at)));
    if (s.override_name) main.append(el("span", { class: "cp-slot-name" }, s.override_name));
    if (s.orgs.length) main.append(el("span", { class: "cp-slot-orgs" }, s.orgs.map((o) => o.initials).join(", ")));
    const chip = el("span", { class: "cp-tagchip cp-slotchip" }, main);
    if (mayEdit) {   // inline ✎ segment (a button can't live inside the <a>, so it's a sibling)
      const edit = el("button", { type: "button", class: "cp-slot-edit", title: "Upravit slot (název, orgy)" }, "✎");
      edit.addEventListener("click", () => openSlotOrgs(s));
      chip.append(edit);
    }
    return chip;
  }

  // shared slot-edit dialog (cpSlotOrgsEdit) — same modal as the timeline editor, here with
  // the name-override field too (the timeline editor has a separate name dialog).
  function openSlotOrgs(s) {
    window.cpSlotOrgsEdit({
      orgs: DATA.orgs,
      selected: s.orgs.map((o) => o.org_id),
      withName: true, name: s.override_name, namePlaceholder: A.title,
      url: withId(U.slot, s.id),
      onSaved: (orgs, _ids, overrideName) => { s.orgs = orgs; s.override_name = overrideName; renderHeader(); },
    });
  }

  // Due indicator for a todo. Completed → the plain date; otherwise the whole-day delta
  // from today: remaining days in green, overdue days in red, due-today neutral.
  function dueBadge(t) {
    if (!t.due_date) return null;
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

  // --- title -----------------------------------------------------------------
  function renderTitle() {
    const h1 = el("h1", { class: "cp-act-title" }, A.title);
    if (mayEdit) {
      const b = el("button", { type: "button", class: "cp-edit-toggle" }, "✎");
      b.addEventListener("click", openIdentityEdit);   // title + category
      h1.append(" ", b);
    }
    titleHost.replaceChildren(h1);
  }

  function openIdentityEdit() {
    const title = el("input", { type: "text", class: "cp-modal-name", value: A.title });
    const cats = chipGroup(DATA.categories.map((c) => [c.id, swatch(c.color), c.label]), { selected: A.category_id });
    const cancel = el("button", { type: "button", class: "cp-cancel" }, "Zrušit");
    const ok = el("button", { type: "button", class: "cp-primary" }, "Uložit");
    const dialog = el("div", { class: "cp-modal cp-modal-wide" },
      el("div", { class: "cp-modal-head" }, "Upravit aktivitu"),
      el("div", { class: "cp-pane" },
        el("label", { class: "cp-field-label" }, "Název"), title,
        el("label", { class: "cp-field-label" }, "Kategorie"), cats.node),
      el("div", { class: "cp-modal-foot" }, cancel, ok));
    const close = openModal(dialog);
    cancel.addEventListener("click", close);
    ok.addEventListener("click", async () => {
      const t = title.value.trim();
      if (!t) { title.focus(); return; }
      submit(ok, async () => { const j = await api("PATCH", U.update, { title: t, category_id: cats.get() }); Object.assign(A, j.activity); close(); renderTitle(); renderHeader(); toast("Uloženo"); });
    });
    title.focus();
  }

  // --- header block (category / orgs / tags) ---------------------------------
  function headerRow(label, content, onEdit) {
    const row = el("div", { class: "cp-act-row" },
      el("span", { class: "cp-act-row-label" }, label),
      el("div", { class: "cp-act-row-body" }, content));
    if (onEdit) {
      const b = el("button", { type: "button", class: "cp-edit-toggle cp-act-row-edit" }, "✎");
      b.addEventListener("click", onEdit);
      row.append(b);
    }
    return row;
  }

  function renderHeader() {
    const bar = el("div", { class: "cp-act-bar" });
    const cat = catById[A.category_id];
    bar.append(headerRow("Kategorie",
      cat ? el("span", { class: "cp-cat-badge" }, swatch(cat.color), cat.label) : el("span", { class: "cp-muted" }, "—")));
    bar.append(headerRow("Orgové", orgsLine(), mayEdit && openOrgsEdit));
    const chips = el("div", { class: "cp-tagchips" });
    if (A.tags.length) A.tags.forEach((t) => chips.append(tagChip(t)));
    else chips.append(el("span", { class: "cp-muted" }, "—"));
    bar.append(headerRow("Tagy", chips, mayEdit && openTagsEdit));
    const allSlots = A.slots.slice().sort((a, b) => a.start_at.localeCompare(b.start_at));
    const slots = el("div", { class: "cp-slot-list" });
    if (!allSlots.length) {
      slots.append(el("span", { class: "cp-muted" }, "Žádné sloty"));
    } else {
      // >3 slots collapse to the first 3 behind a toggle (count then shown in the label)
      const shown = allSlots.length > 3 && !slotsExpanded ? allSlots.slice(0, 3) : allSlots;
      shown.forEach((s) => slots.append(slotChip(s)));
      if (allSlots.length > 3) {
        const toggle = el("button", { type: "button", class: "cp-slot-toggle" }, slotsExpanded ? "skryj" : "… ukaž všechny");
        toggle.addEventListener("click", () => { slotsExpanded = !slotsExpanded; renderHeader(); });
        slots.append(toggle);
      }
    }
    bar.append(headerRow(allSlots.length > 3 ? `Sloty (${allSlots.length})` : "Sloty", slots));
    headerHost.replaceChildren(bar);
  }

  function orgsLine() {
    const part = (label, role) => {
      const list = A.orgs.filter((o) => o.role === role);
      if (!list.length) return null;
      return el("span", { class: "cp-org-part" },
        el("span", { class: "cp-org-role" }, label), list.map((o) => o.initials).join(", "));
    };
    const g = part("Garant", "garant"), h = part("Pomocník", "helper");
    if (!g && !h) return el("span", { class: "cp-muted" }, "—");
    return el("span", { class: "cp-org-line" }, g, h);   // el skips the null part
  }

  // --- tag chips (read-only display; value sits in the right half of the chip) ---
  function tagChip(t) {
    const chip = el("span", { class: "cp-tagchip kind-" + t.kind + (t.pinned ? " pinned" : "") },
      el("span", { class: "cp-tagchip-name" }, t.name));
    if (t.kind === "check") {
      const on = t.value === "true";
      chip.append(el("span", { class: "cp-tagchip-val" + (on ? " yes" : " no") }, on ? "✓" : "✗"));
    } else if (t.kind === "progress") {
      const pct = clampPct(t.value);
      chip.append(el("span", { class: "cp-tagchip-bar" },
        el("span", { class: "cp-tagchip-fill", style: "width:" + pct + "%" }),
        el("span", { class: "cp-tagchip-num" }, pct + " %")));
    } else if (t.kind === "text") {
      chip.append(el("span", { class: "cp-tagchip-text" }, t.value || "—"));
    }
    // kind "label": name only
    return chip;
  }

  function openOrgsEdit() {
    if (!DATA.orgs.length) { toast("Žádní orgové — přidejte je v nastavení akce.", true); return; }
    const entries = () => DATA.orgs.map((o) => [o.id, el("b", null, o.initials), " " + o.name]);
    const idsWith = (role) => A.orgs.filter((o) => o.role === role).map((o) => o.org_id);
    const garants = chipGroup(entries(), { multi: true, selected: idsWith("garant") });
    const helpers = chipGroup(entries(), { multi: true, selected: idsWith("helper") });
    const cancel = el("button", { type: "button", class: "cp-cancel" }, "Zrušit");
    const ok = el("button", { type: "button", class: "cp-primary" }, "Uložit");
    const dialog = el("div", { class: "cp-modal cp-modal-wide" },
      el("div", { class: "cp-modal-head" }, "Orgové aktivity"),
      el("div", { class: "cp-pane" },
        el("label", { class: "cp-field-label" }, "Garanti"), garants.node,
        el("label", { class: "cp-field-label" }, "Pomocníci"), helpers.node),
      el("div", { class: "cp-modal-foot" }, cancel, ok));
    const close = openModal(dialog);
    cancel.addEventListener("click", close);
    ok.addEventListener("click", async () => {
      const orgs = [
        ...garants.get().map((id) => ({ org_id: id, role: "garant" })),
        ...helpers.get().map((id) => ({ org_id: id, role: "helper" })),
      ];
      submit(ok, async () => { const j = await api("PUT", U.orgs, { orgs }); A.orgs = j.orgs; close(); renderHeader(); toast("Uloženo"); });
    });
  }

  // iOS-style on/off switch wrapping a checkbox; returns { node, input }.
  function toggleSwitch(checked) {
    const input = el("input", { type: "checkbox", class: "cp-switch-input" });
    input.checked = checked;
    return { node: el("label", { class: "cp-switch" }, input, el("span", { class: "cp-switch-slider" })), input };
  }

  // One editor row: an enable switch, the tag name, and a kind-specific value control
  // (disabled + greyed until enabled). read() → { tag_id, enabled, value } for the batch save.
  function tagEditRow(d, enabled, value) {
    const sw = toggleSwitch(enabled);
    let valEl = null, valWrap = null, readVal = () => null;
    if (d.kind === "check") {
      valEl = el("input", { type: "checkbox" });
      valEl.checked = value === "true";
      readVal = () => (valEl.checked ? "true" : "false");
      valWrap = valEl;
    } else if (d.kind === "progress") {
      valEl = el("input", { type: "range", min: 0, max: 100, class: "cp-slider" });
      valEl.value = clampPct(value);
      const num = el("span", { class: "cp-slider-num" }, valEl.value + " %");
      valEl.addEventListener("input", () => { num.textContent = valEl.value + " %"; });
      readVal = () => String(clampPct(valEl.value));
      valWrap = el("span", { class: "cp-slider-wrap" }, valEl, num);
    } else if (d.kind === "text") {
      valEl = el("input", { type: "text" });
      valEl.value = value || "";
      readVal = () => valEl.value || null;
      valWrap = valEl;
    }
    const row = el("div", { class: "cp-tagedit-row" }, sw.node,
      el("span", { class: "cp-tagedit-name" }, d.name + (d.pinned ? " 📌" : "")));
    if (valWrap) row.append(el("span", { class: "cp-tagedit-val" }, valWrap));
    const sync = () => { row.classList.toggle("disabled", !sw.input.checked); if (valEl) valEl.disabled = !sw.input.checked; };
    sw.input.addEventListener("change", sync);
    sync();
    return { node: row, read: () => ({ tag_id: d.id, enabled: sw.input.checked, value: valWrap ? readVal() : null }) };
  }

  function openTagsEdit() {
    if (!DATA.tag_defs.length) { toast("Žádné tagy — vytvořte je v nastavení akce.", true); return; }
    const value = Object.fromEntries(A.tags.map((t) => [t.tag_id, t.value]));
    const enabled = new Set(A.tags.map((t) => t.tag_id));
    const rows = DATA.tag_defs.map((d) => tagEditRow(d, enabled.has(d.id), value[d.id]));
    const cancel = el("button", { type: "button", class: "cp-cancel" }, "Zrušit");
    const ok = el("button", { type: "button", class: "cp-primary" }, "Uložit");
    const dialog = el("div", { class: "cp-modal cp-modal-wide" },
      el("div", { class: "cp-modal-head" }, "Tagy aktivity"),
      el("div", { class: "cp-pane" },
        el("p", { class: "cp-muted" }, "Zapni tagy zobrazené u této aktivity a nastav jejich hodnoty."),
        el("div", { class: "cp-tagedit-list" }, ...rows.map((r) => r.node))),
      el("div", { class: "cp-modal-foot" }, cancel, ok));
    const close = openModal(dialog);
    cancel.addEventListener("click", close);
    ok.addEventListener("click", async () => {
      const tags = rows.map((r) => r.read()).filter((r) => r.enabled).map((r) => ({ tag_id: r.tag_id, value: r.value }));
      submit(ok, async () => { const j = await api("PUT", U.tags, { tags }); A.tags = j.tags; close(); renderHeader(); toast("Uloženo"); });
    });
  }

  // --- tabs ------------------------------------------------------------------
  // The three panes are built once and kept mounted; switching tabs only toggles their
  // visibility, so an open description editor is never destroyed. The tab bar is its own
  // region because its labels carry todo/material counts that change independently.
  function renderTabbar() {
    const doneT = A.todos.filter((t) => t.is_done).length, totT = A.todos.length;
    const doneM = A.material_needs.filter((n) => n.is_ready).length, totM = A.material_needs.length;
    const tabs = [
      { key: "description", label: "Popis" + (descEdit ? " ✎" : "") },   // ✎ = editor open
      { key: "todos", label: "Úkoly" + (totT ? ` (${doneT}/${totT})` : "") },
      { key: "materials", label: "Materiál" + (totM ? ` (${doneM}/${totM})` : "") },
    ];
    tabbarHost.replaceChildren();
    tabs.forEach((tab) => {
      const b = el("button", { type: "button", class: "cp-tabbtn" + (tab.key === activeTab ? " on" : "") }, tab.label);
      b.addEventListener("click", () => {
        activeTab = tab.key; renderTabbar(); showActivePane();
        TAB.write(tab.key);  // reflect the active tab in the URL hash (shareable/reloadable)
      });
      tabbarHost.append(b);
    });
  }

  // Show the active pane, hide the rest (no teardown). CodeMirror mis-measures while its
  // host is display:none, so refresh + refit the editor when its tab becomes visible again.
  function showActivePane() {
    for (const key in panes) panes[key].hidden = key !== activeTab;
    if (activeTab === "description" && descEdit) { descEdit.cm.refresh(); descEdit.fit(); }
  }

  // Read view: rendered markdown + an edit button that swaps in the editor in place.
  function renderDescriptionPane() {
    const pane = panes.description;
    pane.replaceChildren();
    if (mayEdit) {
      const edit = el("button", { type: "button", class: "cp-edit-toggle cp-desc-edit" }, "✎ Upravit popis");
      edit.addEventListener("click", () => startDescEdit(pane));
      pane.append(el("div", { class: "cp-desc-actions cp-desc-actions-float" }, edit));   // floats over the markdown's top-right
    }
    const body = el("div", { class: "cp-markdown" });
    body.innerHTML = A.description_md ? md.render(A.description_md) : '<p class="cp-muted">Bez popisu.</p>';
    pane.append(body);
  }

  // In-place Markdown editor (CodeMirror 5, markdown mode + a small toolbar). Save/Cancel
  // confirm when there are unsaved changes; with no changes they just close the editor.
  function startDescEdit(pane) {
    const original = A.description_md || "";
    const barEl = el("div", { class: "cp-mde-bar" });
    const host = el("div", { class: "cp-mde" });
    const preview = el("div", { class: "cp-mde cp-mde-preview cp-markdown" });   // rendered view (toggle)
    preview.hidden = true;
    const save = el("button", { type: "button", class: "cp-primary" }, "Uložit");
    const cancel = el("button", { type: "button", class: "cp-cancel" }, "Zrušit");
    pane.replaceChildren(barEl, host, preview);

    const cm = CodeMirror(host, {
      value: original, mode: "markdown", lineWrapping: true,
      indentUnit: 2, tabSize: 2, indentWithTabs: false,
      extraKeys: {
        Tab: "indentMore",          // indent the line(s) by 2 spaces → arbitrary list nesting
        "Shift-Tab": "indentLess",
        Enter: "newlineAndIndentContinueMarkdownList",   // auto-continue lists (continuelist addon)
      },
    });

    // toolbar: wrap the selection (bold/italic/code) or toggle a line prefix (heading/quote/list)
    const wrap = (mark) => {
      const sel = cm.getSelection();
      if (sel) { cm.replaceSelection(mark + sel + mark); }
      else { const c = cm.getCursor(); cm.replaceSelection(mark + mark); cm.setCursor({ line: c.line, ch: c.ch + mark.length }); }
      cm.focus();
    };
    const eachSelectedLine = (fn) => {
      const from = cm.getCursor("from").line, to = cm.getCursor("to").line;
      for (let l = from, i = 0; l <= to; l++, i++) {
        const text = cm.getLine(l);
        cm.replaceRange(fn(text, i), { line: l, ch: 0 }, { line: l, ch: text.length });
      }
      cm.focus();
    };
    const togglePrefix = (pfx) => {
      const from = cm.getCursor("from").line, to = cm.getCursor("to").line;
      let allHave = true;
      for (let l = from; l <= to; l++) if (!cm.getLine(l).startsWith(pfx)) { allHave = false; break; }
      eachSelectedLine((t) => (allHave ? t.slice(pfx.length) : pfx + t));
    };
    const link = () => { cm.replaceSelection("[" + (cm.getSelection() || "odkaz") + "](url)"); cm.focus(); };
    const image = () => { cm.replaceSelection("![" + (cm.getSelection() || "popis") + "](url)"); cm.focus(); };
    const codeBlock = () => { cm.replaceSelection("```\n" + cm.getSelection() + "\n```"); cm.focus(); };
    const hr = () => { cm.replaceSelection("\n---\n"); cm.focus(); };
    // set the selected lines to heading `level` (1–3); clicking the current level removes it
    const setHeading = (level) => {
      const pfx = "#".repeat(level) + " ";
      eachSelectedLine((t) => {
        const body = t.replace(/^#{1,6}\s+/, "");        // strip any existing heading marker
        return t.startsWith(pfx) ? body : pfx + body;    // same level → toggle off; else (re)apply
      });
    };

    // inline SVG icons (Lucide, MIT), grouped with separators like a modern editor
    const svg = (p) => '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor"' +
      ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + p + "</svg>";
    const ICON = {
      bold: '<path d="M14 12a4 4 0 0 0 0-8H6v8"/><path d="M15 20a4 4 0 0 0 0-8H6v8Z"/>',
      italic: '<line x1="19" x2="10" y1="4" y2="4"/><line x1="14" x2="5" y1="20" y2="20"/><line x1="15" x2="9" y1="4" y2="20"/>',
      strikethrough: '<path d="M16 4H9a3 3 0 0 0-2.83 4"/><path d="M14 12a4 4 0 0 1 0 8H6"/><line x1="4" x2="20" y1="12" y2="12"/>',
      heading1: '<path d="M4 12h8"/><path d="M4 18V6"/><path d="M12 18V6"/><path d="m17 12 3-2v8"/>',
      heading2: '<path d="M4 12h8"/><path d="M4 18V6"/><path d="M12 18V6"/><path d="M21 18h-4c0-4 4-3 4-6 0-1.5-2-2.5-4-1"/>',
      heading3: '<path d="M4 12h8"/><path d="M4 18V6"/><path d="M12 18V6"/><path d="M17.5 10.5c1.7-1 3.5 0 3.5 1.5a2 2 0 0 1-2 2"/><path d="M17 17.5c2 1.5 4 .3 4-1.5a2 2 0 0 0-2-2"/>',
      quote: '<path d="M17 6H3"/><path d="M21 12H8"/><path d="M21 18H8"/><path d="M3 12v6"/>',
      list: '<path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/><path d="M3 6h.01"/><path d="M3 12h.01"/><path d="M3 18h.01"/>',
      ordered: '<path d="M11 6h10"/><path d="M11 12h10"/><path d="M11 18h10"/><path d="M4 6h1v4"/><path d="M4 10h2"/><path d="M6 18H4c0-1 2-2 2-3s-1-1.5-2-1"/>',
      code: '<path d="m16 18 6-6-6-6"/><path d="m8 6-6 6 6 6"/>',
      codeblock: '<path d="M10 9.5 8 12l2 2.5"/><path d="m14 9.5 2 2.5-2 2.5"/><rect width="18" height="18" x="3" y="3" rx="2"/>',
      link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
      image: '<rect width="18" height="18" x="3" y="3" rx="2" ry="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>',
      hr: '<line x1="3" x2="21" y1="12" y2="12"/><polyline points="8 8 12 4 16 8"/><polyline points="16 16 12 20 8 16"/>',
      undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
      redo: '<path d="m15 14 5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/>',
      preview: '<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>',
    };
    // preview toggle: swap the editor for the rendered markdown. Formatting buttons are
    // disabled while previewing (nothing visible to act on); Save/Cancel stay live.
    let previewing = false;
    const togglePreview = () => {
      previewing = !previewing;
      if (previewing) {
        const src = cm.getValue();
        preview.innerHTML = src ? md.render(src) : '<p class="cp-muted">Bez popisu.</p>';
        preview.style.height = host.offsetHeight + "px";   // match the editor's current height
        host.hidden = true; preview.hidden = false;
      } else {
        preview.hidden = true; host.hidden = false;
        cm.refresh(); cm.focus();
      }
      for (const key in btnByKey) if (key !== "preview") btnByKey[key].disabled = previewing;
      btnByKey.preview.classList.toggle("active", previewing);
      if (!previewing) updateActive();   // restore undo/redo enabled-state + active marks
    };
    const GROUPS = [
      [["bold", "Tučně (Ctrl+B)", () => wrap("**")], ["italic", "Kurzíva (Ctrl+I)", () => wrap("*")], ["strikethrough", "Přeškrtnutí", () => wrap("~~")]],
      [["heading1", "Nadpis 1 (Ctrl+1)", () => setHeading(1)], ["heading2", "Nadpis 2 (Ctrl+2)", () => setHeading(2)], ["heading3", "Nadpis 3 (Ctrl+3)", () => setHeading(3)], ["quote", "Citace", () => togglePrefix("> ")]],
      [["list", "Odrážky", () => togglePrefix("- ")], ["ordered", "Číslovaný seznam", () => eachSelectedLine((t, i) => (i + 1) + ". " + t)]],
      [["code", "Kód", () => wrap("`")], ["codeblock", "Blok kódu", codeBlock], ["link", "Odkaz (Ctrl+K)", link], ["image", "Obrázek", image], ["hr", "Vodorovná čára", hr]],
      [["undo", "Zpět (Ctrl+Z)", () => cm.undo()], ["redo", "Vpřed (Ctrl+Y)", () => cm.redo()]],
      [["preview", "Náhled", togglePreview]],
    ];
    const btnByKey = {};
    GROUPS.forEach((group, gi) => {
      if (gi) barEl.append(el("span", { class: "cp-mde-sep" }));
      group.forEach(([key, title, action]) => {
        const btn = el("button", { type: "button", class: "cp-mde-btn", title });
        btn.innerHTML = svg(ICON[key]);   // static, trusted SVG markup
        btn.addEventListener("click", action);
        btnByKey[key] = btn;
        barEl.append(btn);
      });
    });
    barEl.append(el("div", { class: "cp-mde-actions" }, save, cancel));   // pushed to the right end
    // reflect the formatting under the caret on the toolbar (inline via token type, block via line)
    const updateActive = () => {
      const tok = cm.getTokenTypeAt(cm.getCursor()) || "";
      const line = cm.getLine(cm.getCursor().line) || "";
      const on = {
        bold: /strong/.test(tok), italic: /\bem\b/.test(tok), strikethrough: /strikethrough/.test(tok),
        code: /comment/.test(tok),
        heading1: /^#\s/.test(line), heading2: /^##\s/.test(line), heading3: /^###\s/.test(line),
        quote: /^\s*>/.test(line),
        list: /^\s*[-*+]\s/.test(line), ordered: /^\s*\d+[.)]\s/.test(line),
      };
      for (const key in btnByKey) btnByKey[key].classList.toggle("active", !!on[key]);
      const h = cm.historySize();   // grey out undo/redo when there's nothing to undo/redo
      btnByKey.undo.disabled = !h.undo;
      btnByKey.redo.disabled = !h.redo;
    };
    cm.on("cursorActivity", updateActive);
    cm.on("change", updateActive);   // history depth changes on edits (and on undo/redo itself)
    updateActive();
    // keyboard shortcuts (Ctrl on Win/Linux, Cmd on Mac); undo/redo are CM defaults already
    const keymap = {};
    const bind = (k, fn) => { keymap["Ctrl-" + k] = fn; keymap["Cmd-" + k] = fn; };
    bind("B", () => wrap("**")); bind("I", () => wrap("*")); bind("K", link);
    bind("1", () => setHeading(1)); bind("2", () => setHeading(2)); bind("3", () => setHeading(3));
    cm.addKeyMap(keymap);

    // grow the editor down to the bottom of the window; re-fit on resize (self-removing)
    const fit = () => {
      if (!host.isConnected) { window.removeEventListener("resize", fit); return; }
      if (host.offsetParent === null) return;   // hidden (another tab is active) — refit when shown
      cm.setSize(null, Math.max(240, window.innerHeight - host.getBoundingClientRect().top - 16));
    };
    fit();
    window.addEventListener("resize", fit);
    cm.focus();

    const dirty = () => cm.getValue() !== original;
    descEdit = { cm, fit, dirty };         // editor persists across tab switches; showActivePane refits it
    renderTabbar();                        // mark the Popis tab as having an open editor (✎)
    // Save is enabled only while there are unsaved changes
    const syncSave = () => { save.disabled = !dirty(); };
    cm.on("change", syncSave);
    syncSave();
    // warn on full page-leave (reload / navigate away / close) with unsaved changes
    const beforeUnload = (e) => { if (dirty()) { e.preventDefault(); e.returnValue = ""; } };
    window.addEventListener("beforeunload", beforeUnload);
    const close = () => {
      window.removeEventListener("resize", fit);
      window.removeEventListener("beforeunload", beforeUnload);
      descEdit = null;
      renderDescriptionPane();   // back to the read view (only this pane)
      renderTabbar();            // drop the ✎ marker from the Popis tab
    };
    cancel.addEventListener("click", () => {
      if (dirty() && !confirm("Zahodit změny popisu?")) return;
      close();
    });
    save.addEventListener("click", async () => {
      if (!dirty()) return;                // disabled when unchanged; guard anyway. No confirm — just save.
      save.disabled = true;
      const content = cm.getValue();
      try { const j = await api("PATCH", U.update, { description_md: content || null }); Object.assign(A, j.activity); close(); toast("Uloženo"); }
      catch (e) { save.disabled = false; toast(e.message, true); }
    });
  }

  // --- todos -----------------------------------------------------------------
  function renderTodosPane() {
    const list = el("div", { class: "cp-todo-list" });
    if (!A.todos.length) list.append(el("p", { class: "cp-muted" }, "Žádné úkoly."));
    A.todos.forEach((t) => list.append(todoRow(t)));
    if (mayEdit) {
      const add = el("button", { type: "button", class: "cp-add cp-todo-add" }, "+ Přidat úkol");
      add.addEventListener("click", () => openTodoForm(null));
      list.append(add);
    }
    panes.todos.replaceChildren(list);
  }
  const refreshTodos = () => { renderTodosPane(); renderTabbar(); };   // counts live in the tab label

  function todoRow(t) {
    const cb = el("input", { type: "checkbox" });
    cb.checked = t.is_done;
    cb.disabled = !mayEdit;
    if (mayEdit) cb.addEventListener("change", async () => {
      try { const j = await api("PATCH", withId(U.todoItem, t.id), { is_done: cb.checked }); Object.assign(t, j.todo); refreshTodos(); }
      catch (e) { cb.checked = !cb.checked; toast(e.message, true); }
    });
    const title = el("span", { class: "cp-todo-title" + (t.is_done ? " done" : "") }, t.title);
    const line = el("div", { class: "cp-todo-line" }, title);
    const due = dueBadge(t);
    if (due) line.append(due);
    if (mayEdit) {
      const edit = el("button", { type: "button", class: "cp-mini", title: "Upravit" }, "✎");
      edit.addEventListener("click", () => openTodoForm(t));
      const del = el("button", { type: "button", class: "cp-danger cp-mini", title: "Smazat" }, "✕");
      del.addEventListener("click", async () => {
        if (!confirm("Smazat úkol?")) return;
        try { await api("DELETE", withId(U.todoItem, t.id)); A.todos = A.todos.filter((x) => x.id !== t.id); refreshTodos(); toast("Smazáno"); }
        catch (e) { toast(e.message, true); }
      });
      line.append(edit, del);
    }
    const main = el("div", { class: "cp-todo-main" }, line);
    if (t.note) main.append(el("div", { class: "cp-muted cp-todo-note" }, t.note));   // note on its own line
    return el("div", { class: "cp-todo-row" }, cb, main);
  }

  // shared add/edit form — `todo` null = create, otherwise edit
  function openTodoForm(todo) {
    const seed = todo || {};
    const title = el("input", { type: "text", class: "cp-modal-name", value: seed.title || "" });
    const note = el("textarea", { class: "cp-act-textarea", rows: 3 });
    note.value = seed.note || "";
    const due = el("input", { type: "date" });
    if (seed.due_date) due.value = seed.due_date;
    const cancel = el("button", { type: "button", class: "cp-cancel" }, "Zrušit");
    const ok = el("button", { type: "button", class: "cp-primary" }, todo ? "Uložit" : "Přidat");
    const dialog = el("div", { class: "cp-modal cp-modal-wide" },
      el("div", { class: "cp-modal-head" }, todo ? "Upravit úkol" : "Nový úkol"),
      el("div", { class: "cp-pane" },
        el("label", { class: "cp-field-label" }, "Název"), title,
        el("label", { class: "cp-field-label" }, "Poznámka"), note,
        el("label", { class: "cp-field-label" }, "Termín"), due),
      el("div", { class: "cp-modal-foot" }, cancel, ok));
    const close = openModal(dialog);
    cancel.addEventListener("click", close);
    ok.addEventListener("click", async () => {
      const v = title.value.trim();
      if (!v) { title.focus(); return; }
      const body = { title: v, note: note.value || null, due_date: due.value || null };
      submit(ok, async () => {
        if (todo) { const j = await api("PATCH", withId(U.todoItem, todo.id), body); Object.assign(todo, j.todo); }
        else { const j = await api("POST", U.todoCreate, body); A.todos.push(j.todo); }
        close();
        refreshTodos();
        toast("Uloženo");
      });
    });
    title.focus();
  }

  // --- materials -------------------------------------------------------------
  function renderMaterialsPane() {
    const list = el("div", { class: "cp-need-list" });
    if (!A.material_needs.length) list.append(el("p", { class: "cp-muted" }, "Žádný materiál."));
    A.material_needs.forEach((n) => list.append(needRow(n)));
    if (mayEdit) {
      const add = el("button", { type: "button", class: "cp-add cp-need-add" }, "+ Přidat materiál");
      add.addEventListener("click", openMaterialPicker);
      list.append(add);
    }
    panes.materials.replaceChildren(list);
  }
  const refreshMaterials = () => { renderMaterialsPane(); renderTabbar(); };   // counts live in the tab label

  function needRow(n) {
    const cb = el("input", { type: "checkbox" });
    cb.checked = n.is_ready;
    cb.disabled = !mayEdit;
    if (mayEdit) cb.addEventListener("change", async () => {
      try { const j = await api("PATCH", withId(U.needItem, n.id), { is_ready: cb.checked }); Object.assign(n, j.need); refreshMaterials(); }
      catch (e) { cb.checked = !cb.checked; toast(e.message, true); }
    });
    // name → this material in the camp-wide overview (highlighted there); keep the external
    // catalog url as a small ↗ alongside it when present.
    const nameCell = el("span", { class: "cp-need-name" },
      el("a", { href: U.materialsOverview + "#material-" + n.material.id }, n.material.name));
    if (n.material.url)
      nameCell.append(" ", el("a", { href: n.material.url, target: "_blank", rel: "noopener", class: "cp-ext-link", title: "Externí odkaz" }, "↗"));
    const qty = ((n.amount != null ? n.amount : "") + " " + (n.unit || n.material.unit || "")).trim();
    const line = el("div", { class: "cp-need-line" },
      nameCell,
      el("span", { class: "cp-muted cp-need-qty" }, qty));
    if (mayEdit) {
      const edit = el("button", { type: "button", class: "cp-mini", title: "Upravit" }, "✎");
      edit.addEventListener("click", () => openNeedEdit(n));
      const del = el("button", { type: "button", class: "cp-danger cp-mini", title: "Odebrat" }, "✕");
      del.addEventListener("click", async () => {
        if (!confirm("Odebrat materiál?")) return;
        try { await api("DELETE", withId(U.needItem, n.id)); A.material_needs = A.material_needs.filter((x) => x.id !== n.id); refreshMaterials(); toast("Odebráno"); }
        catch (e) { toast(e.message, true); }
      });
      line.append(edit, del);
    }
    const main = el("div", { class: "cp-need-main" }, line);
    if (n.note) main.append(el("div", { class: "cp-muted cp-need-note" }, n.note));   // note on its own line
    return el("div", { class: "cp-need-row" }, cb, main);
  }

  // need-detail fields (shared by add + edit); returns { pane, read() }. `defaultUnit` is
  // the material's catalog unit: shown as the unit placeholder, only overridden if it differs.
  function needFields(seed, defaultUnit) {
    const amount = el("input", { type: "number", step: "any", class: "cp-num", placeholder: "množství" });
    if (seed.amount != null) amount.value = seed.amount;
    const unit = el("input", { type: "text", class: "cp-need-unit", placeholder: defaultUnit || "jednotka" });
    unit.value = seed.unit || "";
    const note = el("input", { type: "text" });
    note.value = seed.note || "";
    const pane = el("div", { class: "cp-pane" },
      el("label", { class: "cp-field-label" }, "Množství a jednotka"),
      el("div", { class: "cp-need-amount-row" }, amount, unit),
      el("div", { class: "cp-field-hint" }, "Jednotku zadej jen pokud se liší od výchozí."),
      el("label", { class: "cp-field-label" }, "Poznámka"), note);
    const read = () => ({
      amount: amount.value === "" ? null : Number(amount.value),
      unit: unit.value || null,
      note: note.value || null,
    });
    return { pane, read };
  }

  function openNeedEdit(n) {
    // shared dialog (cpMaterialNeedEdit) — same edit window as the camp-wide materials overview
    window.cpMaterialNeedEdit({
      title: n.material.name, need: n, defaultUnit: n.material.unit,
      url: withId(U.needItem, n.id),
      onSaved: (need) => { Object.assign(n, need); refreshMaterials(); },
    });
  }

  // create a new catalog material (name + default unit / note / url), then continue
  function openMaterialCreate(presetName, onCreated) {
    const name = el("input", { type: "text", class: "cp-modal-name", value: presetName || "" });
    const unit = el("input", { type: "text" });
    const note = el("textarea", { class: "cp-act-textarea", rows: 3 });
    const url = el("input", { type: "url", placeholder: "https://…" });
    const cancel = el("button", { type: "button", class: "cp-cancel" }, "Zrušit");
    const ok = el("button", { type: "button", class: "cp-primary" }, "Vytvořit");
    const dialog = el("div", { class: "cp-modal cp-modal-wide" },
      el("div", { class: "cp-modal-head" }, "Nový materiál"),
      el("div", { class: "cp-pane" },
        el("label", { class: "cp-field-label" }, "Název"), name,
        el("label", { class: "cp-field-label" }, "Výchozí jednotka"), unit,
        el("label", { class: "cp-field-label" }, "Poznámka"), note,
        el("label", { class: "cp-field-label" }, "Odkaz"), url),
      el("div", { class: "cp-modal-foot" }, cancel, ok));
    const close = openModal(dialog);
    cancel.addEventListener("click", close);
    ok.addEventListener("click", async () => {
      const nm = name.value.trim();
      if (!nm) { name.focus(); return; }
      submit(ok, async () => {
        const j = await api("POST", U.materialCreate,
          { name: nm, unit: unit.value || null, note: note.value || null, url: url.value || null });
        if (catalogCache) catalogCache.unshift(j.material);
        close();
        onCreated(j.material);
      });
    });
    name.focus();
  }

  // step 2 of adding: amount/unit/note for the chosen catalog material, then POST
  function openNeedAdd(material) {
    const f = needFields({}, material.unit);
    const cancel = el("button", { type: "button", class: "cp-cancel" }, "Zrušit");
    const ok = el("button", { type: "button", class: "cp-primary" }, "Přidat");
    const dialog = el("div", { class: "cp-modal cp-modal-wide" },
      el("div", { class: "cp-modal-head" }, "Přidat „" + material.name + "“"), f.pane,
      el("div", { class: "cp-modal-foot" }, cancel, ok));
    const close = openModal(dialog);
    cancel.addEventListener("click", close);
    ok.addEventListener("click", async () => {
      submit(ok, async () => { const j = await api("POST", U.needCreate, { material_id: material.id, ...f.read() }); A.material_needs.push(j.need); close(); refreshMaterials(); toast("Uloženo"); });
    });
  }

  // step 1 of adding: pick an existing catalog material (fuzzy) or create a new one
  let catalogCache = null;
  function openMaterialPicker() {
    const search = el("input", { type: "text", class: "cp-modal-search", placeholder: "Hledat materiál…" });
    const list = el("div", { class: "cp-modal-list" });
    const cancel = el("button", { type: "button", class: "cp-cancel" }, "Zrušit");
    const dialog = el("div", { class: "cp-modal" },
      el("div", { class: "cp-modal-head" }, "Přidat materiál"),
      el("div", { class: "cp-pane" }, search, list),
      el("div", { class: "cp-modal-foot" }, cancel));
    const close = openModal(dialog);
    cancel.addEventListener("click", close);

    // keyboard-navigable list (cpDom.keyList): the "+ Vytvořit" row is just another entry.
    const setRows = keyList(search);
    function renderResults() {
      const all = catalogCache || [];
      const q = search.value.trim();
      const matches = q && window.cpFuzzy ? window.cpFuzzy.filter(q, all, (m) => m.name) : all;
      const entries = matches.map((m) => ({
        el: el("button", { type: "button", class: "cp-modal-item" },
          el("span", null, m.name), m.unit ? el("span", { class: "cp-modal-recent" }, m.unit) : null),
        pick: () => { close(); openNeedAdd(m); },
      }));
      if (q && !all.some((m) => m.name.toLowerCase() === q.toLowerCase())) {
        entries.push({
          el: el("button", { type: "button", class: "cp-modal-item" }, el("b", null, "+ Vytvořit „" + q + "“")),
          pick: () => { close(); openMaterialCreate(q, openNeedAdd); },
        });
      }
      list.replaceChildren(...entries.map((e) => e.el));
      if (!entries.length) list.append(el("div", { class: "cp-muted" }, "Katalog je prázdný — napiš název a vytvoř."));
      setRows(entries);
    }
    search.addEventListener("input", renderResults);
    const load = catalogCache ? Promise.resolve() : api("GET", U.materialList).then((j) => { catalogCache = j.materials || []; });
    load.then(renderResults).catch(() => { catalogCache = catalogCache || []; renderResults(); });
    search.focus();
  }

  // --- render ----------------------------------------------------------------
  // Build the page once into stable region nodes, then let each region refresh on its own.
  // An open description editor lives in panes.description and is only ever rebuilt by its
  // own Save/Cancel — never by a tab switch, tag/org edit, or todo/material change.
  function buildShell() {
    titleHost = el("div");
    headerHost = el("div");
    tabbarHost = el("div", { class: "cp-tabbar" });
    panes.description = el("div", { class: "cp-tabpane cp-desc-pane" });
    panes.todos = el("div", { class: "cp-tabpane" });
    panes.materials = el("div", { class: "cp-tabpane" });
    mount.replaceChildren(titleHost, headerHost,
      el("div", { class: "cp-tabs" }, tabbarHost, panes.description, panes.todos, panes.materials));
    renderTitle();
    renderHeader();
    renderTabbar();
    renderDescriptionPane();
    renderTodosPane();
    renderMaterialsPane();
    showActivePane();
  }
  buildShell();
})();
