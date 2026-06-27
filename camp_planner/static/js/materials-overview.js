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

  const { el, api, openModal, chipGroup, keyList, toast, toastNext } = window.cpDom;
  const DATA = JSON.parse(dataEl.textContent);
  const U = DATA.urls;
  const mayEdit = DATA.may_edit;
  const MATS = DATA.materials;              // catalog materials with usages; mutated in place
  const ORGS = DATA.orgs || [];            // camp roster [{id, initials, name}] — edit picker
  const orgName = new Map(ORGS.map((o) => [o.id, o.name]));
  const COLS = mayEdit ? 8 : 7;             // table columns (for the sub-row colspan)

  // Filters: label = free-text query matched against the acquisition label(s) ("" = none);
  // orgIds/noOrg = responsible-org filter (any selected = OR, noOrg = unassigned).
  const filter = { label: "", orgIds: new Set(), noOrg: false };

  const expanded = new Set();               // material ids whose usages are shown (survives row re-render)
  const rowEls = new Map();                 // material id -> { tr, sub, cell } for per-row refresh
  let tbody;                                // stable table body
  let countLabel;                           // "Zobrazeno X z Y" toolbar label
  let openPopover = null;                    // open org-filter dropdown panel (closed on outside click)
  let labelInput;                           // the "Pořízení" header label search <input>

  // --- small helpers ---------------------------------------------------------
  const withId = (tpl, id) => tpl.replace(/\d+$/, id);                       // swap the trailing 0 sentinel
  const mergeUrl = (id) => U.materialMerge.replace(/\/0\/merge$/, "/" + id + "/merge");  // .../<id>/merge

  // Split an acquisition label into a scoped "prefix: value" pair (on the first colon, both
  // sides non-empty) or null when it's a plain label. Whitespace-trimmed.
  function scoped(label) {
    const s = (label || "").trim();
    const i = s.indexOf(":");
    if (i <= 0 || i >= s.length - 1) return null;
    const prefix = s.slice(0, i).trim(), value = s.slice(i + 1).trim();
    return prefix && value ? { prefix, value } : null;
  }

  // Deterministic hue (0–359) from a prefix, so the same prefix always gets the same color.
  function prefixHue(s) {
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360;
    return h;
  }

  // Fold case + diacritics for accent-insensitive search ("sklad" matches "Sklad").
  const norm = (s) => s.toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
  // Canonical search form of a label/query: norm + drop ALL whitespace, so the box matches
  // anywhere in the whole label ignoring spaces ("alza" and "kup:alza" both match "kup: alza").
  const searchForm = (s) => norm(s).replace(/\s+/g, "");

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
    // `max` materials (shared/reusable, e.g. projectors) take the largest single need; the
    // default `sum` adds them up (consumables). Amounts are non-negative, so init 0 fits both.
    const combine = m.sum_strategy === "max" ? Math.max : (a, b) => a + b;
    for (const u of m.usages) {
      if (u.amount == null) continue;
      const unit = (u.unit || m.unit || "").trim();
      if (!sums.has(unit)) { sums.set(unit, 0); order.push(unit); }
      sums.set(unit, combine(sums.get(unit), u.amount));
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
    openPopover = null;   // header is rebuilt → drop any stale popover reference
    const headRow = el("tr", null,
      el("th", null, "Materiál"), el("th", null, "Jednotka"), el("th", null, "Množství"),
      acqHead(), orgsHead(),
      el("th", null, "Aktivit"), el("th", null, "Hotovo"));
    if (mayEdit) headRow.append(el("th", { class: "cp-actions" }, ""));
    tbody = el("tbody");
    // Fixed column widths so the layout doesn't reflow ("jump") as filtering changes which rows
    // (and thus which widest cell) are visible. Order matches the header; last width = actions col.
    const widths = ["22%", "7%", "9%", "28%", "12%", "7%", "8%"];
    if (mayEdit) widths.push("7%");
    const colgroup = el("colgroup", null, ...widths.map((w) => el("col", { style: "width:" + w })));
    countLabel = el("span", { class: "cp-muted cp-todo-count" });   // margin-left:auto hugs it right
    const reset = el("button", { type: "button", class: "cp-mini" }, "Zrušit filtry");
    reset.addEventListener("click", resetFilters);
    mount.replaceChildren(
      el("div", { class: "cp-todo-toolbar" }, countLabel, reset),
      el("table", { class: "cp-table cp-mat-table" }, colgroup, el("thead", null, headRow), tbody));
    renderTable();
  }

  // "Pořízení" header: a free-text box that filters by the acquisition label.
  function acqHead() {
    labelInput = el("input", { type: "search", class: "cp-th-filter", placeholder: "Štítek, např. kup:mefisto…" });
    labelInput.value = filter.label;
    labelInput.addEventListener("input", () => {
      filter.label = labelInput.value;
      writeHash(); renderTable();
    });
    return el("th", null, el("span", { class: "cp-th-label" }, "Pořízení"), labelInput);
  }

  const onFilterChange = () => { writeHash(); renderTable(); };

  // Orgs filter — the same dropdown control the TODO overview uses (checkbox list, any selected
  // = OR; "bez orgů" matches materials with no responsible org).
  function orgsHead() {
    const btn = el("button", { type: "button", class: "cp-th-filter cp-th-dd-btn" });
    const count = () => filter.orgIds.size + (filter.noOrg ? 1 : 0);
    const setLabel = () => { const n = count(); btn.textContent = n ? "Orgové (" + n + ") ▾" : "Vše ▾"; };
    setLabel();
    const panel = el("div", { class: "cp-th-pop", hidden: true });
    panel.addEventListener("click", (e) => e.stopPropagation());
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
    filter.label = ""; filter.orgIds.clear(); filter.noOrg = false;
    writeHash(); buildShell();
  }

  // Materials matching the active label + org filters. The label query is normalised once here,
  // not per material, since it's constant across the filtered set.
  function visibleMaterials() {
    const q = searchForm(filter.label);
    return MATS.filter((m) => matchLabel(m, q) && matchOrgs(m));
  }
  function matchOrgs(m) {
    if (!filter.orgIds.size && !filter.noOrg) return true;
    const orgs = m.orgs || [];
    return orgs.some((o) => filter.orgIds.has(o.org_id)) || (filter.noOrg && !orgs.length);
  }
  function matchLabel(m, q) {
    if (!q) return true;
    return (m.acquisition_labels || []).some((lab) => searchForm(lab).includes(q));
  }

  // --- filter state <-> URL hash ---------------------------------------------
  function writeHash() {
    const p = new URLSearchParams();
    if (filter.label) p.set("label", filter.label);
    filter.orgIds.forEach((id) => p.append("org", id));
    if (filter.noOrg) p.set("noorg", "1");
    const qs = p.toString();
    history.replaceState(null, "", qs ? "#" + qs : location.pathname + location.search);
  }
  function readHash() {
    const p = new URLSearchParams(location.hash.replace(/^#/, ""));
    filter.label = p.get("label") || "";
    filter.orgIds = new Set(p.getAll("org").map(Number).filter((id) => ORGS.some((o) => o.id === id)));
    filter.noOrg = p.get("noorg") === "1";
  }

  function renderTable() {
    tbody.replaceChildren();
    rowEls.clear();
    const visible = visibleMaterials().slice().sort((a, b) => a.name.localeCompare(b.name, "cs"));
    countLabel.textContent = "Zobrazeno " + visible.length + " z " + MATS.length;
    visible.forEach((m) => {
      const r = renderMaterialRow(m);
      rowEls.set(m.id, r);
      tbody.append(r.tr, r.sub);
    });
  }

  // --- acquisition / orgs cells ----------------------------------------------
  // A scoped "prefix: value" label renders as a 2-part tag (prefix part clickable → filters,
  // colored by the prefix's hue); a plain label is a single neutral chip.
  // onRemove (optional, for the editor chips) embeds an ✕ inside the tag that removes it.
  function labelTag(label, onRemove) {
    const x = onRemove && el("button", { type: "button", class: "cp-mat-tag-x", title: "Odebrat" }, "✕");
    // mousedown (not click) + preventDefault: keeps focus on the input so its blur→commit→
    // renderChips rebuild doesn't detach this button before the click would land.
    if (x) x.addEventListener("mousedown", (e) => { e.preventDefault(); e.stopPropagation(); onRemove(); });
    const sc = scoped(label);
    if (!sc) {
      const tag = el("span", { class: "cp-mat-tag cp-mat-tag-plain" }, (label || "").trim());
      if (x) tag.append(x);
      return tag;
    }
    const val = el("span", { class: "cp-mat-tag-val" }, sc.value);
    if (x) val.append(x);
    const tag = el("span", { class: "cp-mat-tag cp-mat-tag-scoped", style: "--h:" + prefixHue(sc.prefix) });
    tag.append(el("span", { class: "cp-mat-tag-pre" }, sc.prefix), val);
    return tag;
  }

  function acqCell(m) {
    const labels = m.acquisition_labels || [];
    if (!labels.length) return el("span", { class: "cp-muted" }, "—");
    const wrap = el("span", { class: "cp-acq-cell" });
    labels.forEach((lab) => wrap.append(labelTag(lab)));
    return wrap;
  }

  function orgsCell(m) {
    const orgs = m.orgs || [];
    if (!orgs.length) return el("span", { class: "cp-muted" }, "—");
    const wrap = el("span");
    orgs.forEach((o, i) => {
      if (i) wrap.append(", ");
      wrap.append(el("span", { title: orgName.get(o.org_id) || "" }, o.initials));
    });
    return wrap;
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
      el("td", null, unitTotals(m) || "—",
        m.sum_strategy === "max"
          ? el("span", { class: "cp-muted cp-mat-agg", title: "Maximum napříč aktivitami" }, " (max)")
          : null),
      el("td", { class: "cp-mat-acq" }, acqCell(m)),
      el("td", { class: "cp-mat-orgs" }, orgsCell(m)),
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

  // Distinct catalog labels for autocomplete, czech-sorted.
  function catalogLabels() {
    const set = new Set();
    MATS.forEach((m) => (m.acquisition_labels || []).forEach((lab) => set.add(lab)));
    return [...set].sort((a, b) => a.localeCompare(b, "cs"));
  }

  // Chips editor for the acquisition labels, autocompleting from existing ones. Enter/Tab/comma
  // commit the typed text, Backspace edits the last chip. Returns { node, get }.
  function chipInput(initial, options) {
    const labels = initial.slice();
    const box = el("div", { class: "cp-chipinput" });
    const input = el("input", { type: "text" });
    const suggest = el("div", { class: "cp-chip-suggest", hidden: true });
    let matches = [], active = -1;

    function renderChips() {
      box.querySelectorAll(".cp-mat-tag").forEach((c) => c.remove());
      labels.forEach((lab, i) => {
        box.insertBefore(labelTag(lab, () => { labels.splice(i, 1); renderChips(); input.focus(); }), input);
      });
      input.placeholder = labels.length ? "" : "Štítek, např. kup: mefisto";
    }
    function commit(value) {
      const v = (value ?? input.value).replace(/,+$/, "").trim();
      if (v && !labels.includes(v)) labels.push(v);
      input.value = ""; closeSuggest(); renderChips();
    }
    function closeSuggest() { suggest.hidden = true; matches = []; active = -1; }
    function highlight(i) {
      const rows = [...suggest.children];
      if (rows[active]) rows[active].classList.remove("cp-active");
      active = i < 0 ? -1 : Math.min(i, rows.length - 1);
      if (rows[active]) rows[active].classList.add("cp-active");
    }
    function renderSuggest() {
      // match whole labels, minus an exact hit or one already added as a chip
      const q = searchForm(input.value);
      matches = q ? options.filter((lab) => {
        const f = searchForm(lab);
        return f.includes(q) && f !== q && !labels.includes(lab);
      }).slice(0, 6) : [];
      if (!matches.length) { closeSuggest(); return; }
      active = -1;
      suggest.replaceChildren(...matches.map((lab) => {
        const row = el("div", { class: "cp-chip-suggest-row" }, lab);
        row.addEventListener("mousedown", (e) => { e.preventDefault(); commit(lab); });
        return row;
      }));
      suggest.hidden = false;
    }
    input.addEventListener("input", renderSuggest);
    input.addEventListener("keydown", (e) => {
      if (!suggest.hidden && e.key === "ArrowDown") { e.preventDefault(); highlight(active + 1); }
      else if (!suggest.hidden && e.key === "ArrowUp") { e.preventDefault(); highlight(active - 1); }
      else if (!suggest.hidden && e.key === "Escape") { e.preventDefault(); closeSuggest(); }
      else if (e.key === "Enter" || e.key === "," || (e.key === "Tab" && input.value.trim())) {
        if (active >= 0) { e.preventDefault(); commit(matches[active]); return; }
        e.preventDefault(); commit();
      } else if (e.key === "Backspace" && !input.value && labels.length) {
        e.preventDefault();
        input.value = labels.pop();   // pull the last chip back into the input to edit it
        renderChips(); renderSuggest();
      }
    });
    input.addEventListener("blur", () => commit());   // commit pending text (e.g. when clicking Save)
    box.append(input, suggest);
    renderChips();
    return { node: box, get: () => labels.slice() };
  }

  function openMaterialEdit(m) {
    const name = el("input", { type: "text", class: "cp-modal-name", value: m.name });
    const unit = el("input", { type: "text", class: "cp-modal-name", value: m.unit || "" });
    const note = el("textarea", { class: "cp-act-textarea", rows: 3 });
    note.value = m.note || "";
    const url = el("input", { type: "url", class: "cp-modal-name", placeholder: "https://…", value: m.url || "" });
    const strat = el("select", { class: "cp-modal-name" },
      el("option", { value: "sum" }, "Součet (výchozí)"),
      el("option", { value: "max" }, "Maximum (sdílené mezi aktivitami)"));
    strat.value = m.sum_strategy || "sum";

    const acq = chipInput(m.acquisition_labels || [], catalogLabels());
    const orgGroup = chipGroup(ORGS.map((o) => [o.id, el("b", null, o.initials), " " + o.name]),
      { multi: true, selected: (m.orgs || []).map((o) => o.org_id) });
    if (!ORGS.length) orgGroup.node.append(el("div", { class: "cp-muted" }, "Žádní orgové — přidejte je v nastavení akce."));

    const { foot, cancel } = modalFoot((ok) => {
      const nm = name.value.trim();
      if (!nm) { name.focus(); return; }
      submit(ok, async () => {
        const j = await api("PATCH", withId(U.materialItem, m.id),
          { name: nm, unit: unit.value || null, note: note.value || null, url: url.value || null,
            acquisition_labels: acq.get(), sum_strategy: strat.value, org_ids: orgGroup.get() });
        Object.assign(m, j.material);   // envelope carries no `usages` → m.usages preserved
        close(); renderTable(); toast("Uloženo");   // renderTable re-sorts (name may have changed); expand state survives

      });
    });
    const dialog = el("div", { class: "cp-modal cp-modal-wide" },
      el("div", { class: "cp-modal-head" }, "Upravit materiál"),
      el("div", { class: "cp-pane" },
        el("label", { class: "cp-field-label" }, "Název"), name,
        el("label", { class: "cp-field-label" }, "Výchozí jednotka"), unit,
        el("label", { class: "cp-field-label" }, "Sčítání množství napříč aktivitami"), strat,
        el("label", { class: "cp-field-label" }, "Štítky pořízení"), acq.node,
        el("div", { class: "cp-field-hint" },
          "Enter, Tab nebo čárka přidá štítek. Backspace vrátí hotový štítek k editaci. " +
          "Tvar „prefix:hodnota“ se zobrazí jako barevný štítek (např. kup:mefisto)."),
        el("label", { class: "cp-field-label" }, "Odpovědní orgové"), orgGroup.node,
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
  else readHash();   // otherwise the hash carries the filters (#label=…&org=…)

  // close the open org-filter dropdown when clicking anywhere outside it
  document.addEventListener("click", () => { if (openPopover) { openPopover.hidden = true; openPopover = null; } });

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
