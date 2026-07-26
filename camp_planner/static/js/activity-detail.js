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

  const { el, api, withId, swatch, openModal, submit, formModal, searchPicker, chipGroup, toast, tabHash } = window.cpDom;
  // html:false escapes raw HTML in the source, so a rendered description can't inject markup.
  const md = window.markdownit({ html: false, linkify: true, breaks: true });
  const DATA = JSON.parse(dataEl.textContent);
  const U = DATA.urls;
  const mayEdit = DATA.may_edit;
  const A = DATA.activity;                  // current state; refreshed in place after saves
  const catById = Object.fromEntries(DATA.categories.map((c) => [c.id, c]));
  // open on the tab named in the URL hash (#todos / #materials / #history), e.g. when
  // arriving from the materials overview; defaults to the description tab.
  const TAB = tabHash(["description", "todos", "materials", "history"]);
  let activeTab = TAB.initial || "description";
  let descEdit = null;                      // {cm, fit, dirty} while the description editor is open
  let titleHost, headerHost, tabbarHost;    // stable region nodes, assigned once in buildShell()
  const panes = {};                         // { description, todos, materials, history } — built once, shown/hidden by tab

  // --- small helpers ---------------------------------------------------------
  const clampPct = (v) => Math.max(0, Math.min(100, parseInt(v, 10) || 0));

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
      const num = el("input", { type: "number", min: 0, max: 100, class: "cp-slider-num" });
      num.value = valEl.value;
      valEl.addEventListener("input", () => { num.value = valEl.value; });           // drag → number
      num.addEventListener("input", () => { valEl.value = num.value; });              // type → slider (live, unclamped)
      num.addEventListener("change", () => { num.value = valEl.value = clampPct(num.value); });  // blur → normalize both
      readVal = () => String(clampPct(valEl.value));
      valWrap = el("span", { class: "cp-slider-wrap" }, valEl, num, el("span", { class: "cp-slider-pct" }, "%"));
    } else if (d.kind === "text") {
      valEl = el("input", { type: "text" });
      valEl.value = value || "";
      readVal = () => valEl.value || null;
      valWrap = valEl;
    }
    const row = el("div", { class: "cp-tagedit-row" }, sw.node,
      el("span", { class: "cp-tagedit-name" }, d.name + (d.pinned ? " 📌" : "")));
    if (valWrap) row.append(el("span", { class: "cp-tagedit-val" }, valWrap));
    const ctrls = !valWrap ? [] : valWrap.matches("input") ? [valWrap] : valWrap.querySelectorAll("input");
    const sync = () => {
      row.classList.toggle("disabled", !sw.input.checked);
      ctrls.forEach((inp) => { inp.disabled = !sw.input.checked; });
    };
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
      { key: "history", label: "Historie změn" },
    ];
    tabbarHost.replaceChildren();
    tabs.forEach((tab) => {
      const b = el("button", { type: "button", class: "cp-tabbtn" + (tab.key === activeTab ? " on" : "") }, tab.label);
      b.addEventListener("click", () => {
        activeTab = tab.key; renderTabbar(); showActivePane();
        TAB.write(tab.key);  // reflect the active tab in the URL hash (preserves any tab's filters)
      });
      tabbarHost.append(b);
    });
  }

  // Show the active pane, hide the rest (no teardown). CodeMirror mis-measures while its
  // host is display:none, so refresh + refit the editor when its tab becomes visible again.
  function showActivePane() {
    for (const key in panes) panes[key].hidden = key !== activeTab;
    if (activeTab === "description" && descEdit) { descEdit.cm.refresh(); descEdit.fit(); }
    // History is append-only on the server; reload its first page on every open so edits
    // made elsewhere on the page (and by Google sync) show up without a full reload.
    if (activeTab === "history") historyFeed.reload();
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

  // In-place Markdown editor — the shared cpMarkdownEdit module (md-editor.js);
  // here we just wire its save to the activity PATCH and keep the tab marker in sync.
  function startDescEdit(pane) {
    descEdit = window.cpMarkdownEdit({
      host: pane, value: A.description_md, md,
      onSave: async (content) => {
        const j = await api("PATCH", U.update, { description_md: content || null });
        Object.assign(A, j.activity);
        toast("Uloženo");
      },
      onClose: () => {
        descEdit = null;
        renderDescriptionPane();   // back to the read view (only this pane)
        renderTabbar();            // drop the ✎ marker from the Popis tab
      },
    });
    renderTabbar();   // mark the Popis tab as having an open editor (✎)
  }

  // --- todos -----------------------------------------------------------------
  // The TODO tab reuses the shared cpTodoList component (same as the camp-wide TODO overview),
  // here scoped to this one activity: no activity column / filter / sort, and no "Zrušit filtry"
  // button (only two filters). Filter state persists in the URL hash after the tab token
  // (#todos&done=1 — hashPrefix keeps the tab segment intact). Built once; it mutates A.todos in
  // place and onChange() keeps the tab label's done/total counts current (renderTabbar reads A.todos).
  function renderTodosPane() {
    window.cpTodoList({
      mount: panes.todos,
      todos: A.todos,
      orgs: DATA.orgs,
      urls: { item: U.todoItem, create: U.todoCreate },
      mayEdit,
      showActivity: false,
      useHash: true,
      resetButton: false,
      hashPrefix: "todos",
      onChange: renderTabbar,
    });
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
    formModal({
      title: "Nový materiál",
      okLabel: "Vytvořit",
      pane: el("div", { class: "cp-pane" },
        el("label", { class: "cp-field-label" }, "Název"), name,
        el("label", { class: "cp-field-label" }, "Výchozí jednotka"), unit,
        el("label", { class: "cp-field-label" }, "Poznámka"), note,
        el("label", { class: "cp-field-label" }, "Odkaz"), url),
      onSubmit: async (close) => {
        const nm = name.value.trim();
        if (!nm) { name.focus(); return; }
        const j = await api("POST", U.materialCreate,
          { name: nm, unit: unit.value || null, note: note.value || null, url: url.value || null });
        if (catalogCache) catalogCache.unshift(j.material);
        close();
        onCreated(j.material);
      },
    });
    name.focus();
  }

  // step 2 of adding: amount/unit/note for the chosen catalog material (the shared
  // material-need dialog in create mode), then POST
  function openNeedAdd(material) {
    window.cpMaterialNeedEdit({
      title: "Přidat „" + material.name + "“",
      defaultUnit: material.unit,
      url: U.needCreate, method: "POST", extraBody: { material_id: material.id },
      okLabel: "Přidat",
      onSaved: (need) => { A.material_needs.push(need); refreshMaterials(); },
    });
  }

  // step 1 of adding: pick an existing catalog material (fuzzy) or create a new one
  let catalogCache = null;
  function openMaterialPicker() {
    const open = () => searchPicker({
      title: "Přidat materiál",
      placeholder: "Hledat materiál…",
      items: catalogCache || [],
      labelOf: (m) => m.name,
      metaOf: (m) => m.unit,
      onPick: (m, close) => { close(); openNeedAdd(m); },
      // the "+ Vytvořit" row is just another entry, offered unless the query exactly exists
      extraEntry: (q) => q && !(catalogCache || []).some((m) => m.name.toLowerCase() === q.toLowerCase())
        ? { label: el("b", null, "+ Vytvořit „" + q + "“"),
            pick: (close) => { close(); openMaterialCreate(q, openNeedAdd); } }
        : null,
      empty: "Katalog je prázdný — napiš název a vytvoř.",
    });
    const load = catalogCache ? Promise.resolve() : api("GET", U.materialList).then((j) => { catalogCache = j.materials || []; });
    load.then(open).catch((e) => { toast(e.message, true); catalogCache = catalogCache || []; open(); });
  }

  // --- change history ("Historie změn") --------------------------------------
  // The feed itself is the shared cpHistoryFeed module; here we just mount it into
  // panes.history filtered to this activity. showActivePane() reloads it on every open so
  // edits made elsewhere on the page — and by Google sync — show up without a full reload.
  let historyFeed = null;

  function renderHistoryPane() {
    historyFeed = window.cpHistoryFeed({
      host: panes.history,
      url: U.audit,
      query: { activity_id: A.id },
      catById,
      typeLabels: DATA.type_labels || {},
    });
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
    panes.history = el("div", { class: "cp-tabpane" });
    mount.replaceChildren(titleHost, headerHost,
      el("div", { class: "cp-tabs" }, tabbarHost,
        panes.description, panes.todos, panes.materials, panes.history));
    renderTitle();
    renderHeader();
    renderTabbar();
    renderDescriptionPane();
    renderTodosPane();
    renderMaterialsPane();
    renderHistoryPane();   // feed shell only; entries are fetched lazily on first open
    showActivePane();
  }
  buildShell();
})();
