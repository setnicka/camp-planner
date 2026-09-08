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

  const { el, api, withId, dash, actionGroup, formModal, mergePicker, searchPicker, orgFilterHead, chipGroup, toast, orgInitials, amountText, amountList, fmtNum, czechKey, segBtn, reveal } = window.cpDom;
  const stock = window.cpStock;
  const DATA = JSON.parse(dataEl.textContent);
  const U = DATA.urls;
  const mayEdit = DATA.may_edit;
  const MATS = DATA.materials;              // catalog materials with usages; mutated in place
  const ORGS = DATA.orgs || [];            // camp roster [{id, initials, name}] — edit picker
  const orgName = new Map(ORGS.map((o) => [o.id, o.name]));
  // Fixed column widths so the layout doesn't reflow ("jump") as filtering changes which rows
  // (and thus which widest cell) are visible. Order matches the header; last width = actions col.
  const WIDTHS = ["25%", "10%", "20%", "20%", "10%", "7%"];
  if (mayEdit) WIDTHS.push("8%");   // three icon segments

  // Filters: label = free-text query matched against the acquisition label(s) ("" = none);
  // orgIds/noOrg = responsible-org filter (any selected = OR, noOrg = unassigned).
  const filter = { label: "", orgIds: new Set(), noOrg: false };

  const expanded = new Set();               // material ids whose usages are shown (survives row re-render)
  const rowEls = new Map();                 // material id -> its <tbody> (summary row + needs) for per-row refresh
  let table;                                // stable table; each material owns one <tbody> under it
  let countLabel;                           // "Zobrazeno X z Y" toolbar label
  let labelInput;                           // the "Štítky" header label search <input>

  // --- small helpers ---------------------------------------------------------
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

  // Canonical search form of a label/query: the shared fold + drop ALL whitespace, so the
  // box matches anywhere in the whole label ignoring spaces ("alza" and "kup:alza" both
  // match "kup: alza").
  const searchForm = (s) => czechKey(s).replace(/\s+/g, "");

  // Per-unit totals across a material's usages, grouped by effective unit (need's override or
  // the catalog default), skipping null amounts → [{unit, amount}] (units in first-seen order).
  function unitSums(m) {
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
    return order.map((unit) => ({ unit, amount: sums.get(unit) }));
  }

  // Units compare after trimming and case-folding; an empty unit is pieces, like "ks".
  const unitKey = (u) => (u || "").trim().toLowerCase() || "ks";
  // Amounts compare as they are shown, so 0.1 + 0.2 covers 0.3 instead of falling a
  // float's hair short of it.
  const shown = (n) => Number(fmtNum(n));

  // The box (a link) or the retired shelf, then the count (countEl).
  function stockCell(m, sums) {
    const it = m.inventory_item;
    if (!it) return el("td", { class: "cp-mat-stock" }, dash());
    // The photo ahead of the box. No placeholder: an empty slot reads as a stray indent.
    return el("td", { class: "cp-mat-stock" }, el("span", { class: "cp-mat-place cp-thumb-row" },
      stock.photo(U, it), el("span", null, stock.place(U, it, m.name), countEl(it, sums))));
  }

  // The thing's count in parentheses, coloured against the summed needs: green covers them,
  // red falls short, grey when the two cannot be compared (units differ, needs in several
  // units). Nothing when nobody counted it.
  function countEl(it, sums) {
    if (it.count == null) return null;
    let cls = "cp-muted", why = "nelze porovnat", mark = "";
    if (stock.retired(it)) {
      why = "věc je vyřazená";
    } else if (!sums.length) {
      why = "žádná potřeba neuvádí množství";
    } else if (sums.length === 1 && unitKey(sums[0].unit) === unitKey(it.unit)) {
      const enough = shown(it.count) >= shown(sums[0].amount);
      cls = enough ? "cp-mat-stock-ok" : "cp-mat-stock-short";
      if (enough) mark = "✓ ";
      why = (enough ? "pokrývá potřebu " : "chybí do potřeby ") + amountText(sums[0].amount, sums[0].unit);
    }
    return el("span", { class: "cp-mat-stock-n " + cls, "data-cp-hint": "", title: why },
      " (" + mark + amountText(it.count, it.unit) + ")");
  }
  const readiness = (m) => ({ ready: m.usages.filter((u) => u.is_ready).length, total: m.usages.length });
  // "Aktivity" cell: ready/total as a filled badge, green when every usage is ready, red
  // while some remain; a dim 0 when the material isn't used anywhere yet.
  function readyBadge(ready, total) {
    if (!total) return el("span", { class: "cp-muted" }, "0");
    const done = ready === total;
    // A title only: a tap hint would swallow the click that expands the row.
    return el("span", { class: "cp-mat-ready " + (done ? "done" : "todo"),
                        title: "hotovo u " + ready + " z " + total + " aktivit" },
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
    // The unit rides along in Množství and the badge in Aktivity carries the activity count,
    // so neither gets a column of its own.
    const headRow = el("tr", null,
      el("th", null, "Materiál"), el("th", null, "Množství"), el("th", null, "Sklad"),
      acqHead(), orgsHead(), el("th", null, "Aktivity"));
    if (mayEdit) headRow.append(el("th", { class: "cp-actions" }, ""));
    const colgroup = el("colgroup", null, ...WIDTHS.map((w) => el("col", { style: "width:" + w })));
    countLabel = el("span", { class: "cp-muted cp-todo-count" });   // margin-left:auto hugs it right
    const reset = el("button", { type: "button", class: "cp-mini" }, "Zrušit filtry");
    reset.addEventListener("click", resetFilters);
    mount.replaceChildren(
      el("div", { class: "cp-todo-toolbar" }, countLabel, reset),
      el("div", { class: "cp-mat-scroll" },
        table = el("table", { class: "cp-table cp-mat-table cp-sticky-head" }, colgroup, el("thead", null, headRow))));
    renderTable();
  }

  // "Štítky" header: a free-text box that filters by the acquisition label.
  function acqHead() {
    labelInput = el("input", { type: "search", class: "cp-th-filter", placeholder: "Štítek, např. kup:mefisto…" });
    labelInput.value = filter.label;
    labelInput.addEventListener("input", () => {
      filter.label = labelInput.value;
      writeHash(); renderTable();
    });
    return el("th", null, el("span", { class: "cp-th-label" }, "Štítky"), labelInput);
  }

  const onFilterChange = () => { writeHash(); renderTable(); };

  // Orgs filter — the shared header dropdown (checkbox list, any selected = OR;
  // "bez garanta" matches materials with no responsible org).
  function orgsHead() {
    return orgFilterHead({
      orgs: ORGS, selected: filter.orgIds, onChange: onFilterChange, label: "Garant",
      extra: { label: "bez garanta", checked: filter.noOrg, set: (v) => { filter.noOrg = v; }, countInLabel: true },
    }).th;
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
    table.querySelectorAll("tbody").forEach((b) => b.remove());
    rowEls.clear();
    const visible = visibleMaterials().slice().sort((a, b) => a.name.localeCompare(b.name, "cs"));
    countLabel.textContent = "Zobrazeno " + visible.length + " z " + MATS.length;
    visible.forEach((m) => {
      const group = renderMaterialRow(m);
      rowEls.set(m.id, group);
      table.append(group);
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
    if (!labels.length) return dash();
    const wrap = el("span", { class: "cp-acq-cell" });
    labels.forEach((lab) => wrap.append(labelTag(lab)));
    return wrap;
  }

  function orgsCell(m) {
    const orgs = m.orgs || [];
    if (!orgs.length) return dash();
    const wrap = el("span");
    orgs.forEach((o, i) => {
      if (i) wrap.append(", ");
      wrap.append(orgInitials(o.initials, orgName.get(o.org_id)));
    });
    return wrap;
  }

  // Rebuild one material's <tbody> (summary row + needs) in place (totals/readiness recompute).
  // Open/closed state lives in `expanded`, so it survives the swap.
  function refreshRow(m) {
    const next = renderMaterialRow(m);
    rowEls.get(m.id).replaceWith(next);
    rowEls.set(m.id, next);
  }

  function renderMaterialRow(m) {
    const { ready, total } = readiness(m);
    const open = expanded.has(m.id);
    const sums = unitSums(m);            // the Množství cell and the stock compare share them
    const tr = el("tr", { class: "cp-mat-row" },
      // A flex row, so a wrapping name stays in its own column beside the caret.
      el("td", null, el("span", { class: "cp-thumb-row" },
        el("span", { class: "cp-mat-caret" }, open ? "▾" : "▸"),
        el("span", null, m.name))),
      el("td", null, amountList(sums) || dash(),
        m.sum_strategy === "max"
          ? el("span", { class: "cp-muted cp-mat-agg", "data-cp-hint": "",
                          title: "Maximum napříč aktivitami" }, " (max)")
          : null),
      stockCell(m, sums),
      el("td", { class: "cp-mat-acq" }, acqCell(m)),
      el("td", { class: "cp-mat-orgs" }, orgsCell(m)),
      el("td", { class: "cp-mat-hotovo" }, readyBadge(ready, total)));
    if (mayEdit) tr.append(el("td", { class: "cp-actions" }, actionGroup([
      { label: "✎", title: "Upravit", onClick: () => openMaterialEdit(m) },
      { label: "⤳", title: "Sloučit s jiným", onClick: () => openMaterialMerge(m) },
      // Greyed while activities use it, saying why (the server refuses the same way).
      m.usages.length
        ? { label: "✕", title: "Smazat", danger: true,
            disabled: "Používají ho aktivity – nejdřív ho slučte s jiným, nebo ho z aktivit odeberte." }
        : { label: "✕", title: "Smazat", danger: true, onClick: (e) => deleteMaterial(m, e.currentTarget) },
    ])));
    // toggle expand on a row click — but not when clicking the name link or an action button
    tr.addEventListener("click", (e) => {
      if (e.target.closest("a, button")) return;
      if (expanded.has(m.id)) expanded.delete(m.id); else expanded.add(m.id);
      refreshRow(m);
    });
    const group = el("tbody", { class: "cp-mat-group" }, tr);
    if (open) group.append(...[infoRow(m), ...m.usages.map((u) => usageRow(m, u))].filter(Boolean));
    return group;
  }

  // The material's url and note, or "nepoužito": one full-width row ahead of the needs;
  // null when there is nothing to say.
  function infoRow(m) {
    const bits = [];
    if (m.url) bits.push(el("div", { class: "cp-mat-note cp-muted" },
      "URL: ", el("a", { href: m.url, target: "_blank", rel: "noopener" }, m.url)));
    if (m.note) bits.push(el("div", { class: "cp-mat-note cp-muted" }, m.note));
    if (!m.usages.length) bits.push(el("div", { class: "cp-usage-empty cp-muted" }, "Zatím nikde nepoužito."));
    return bits.length ? el("tr", { class: "cp-mat-sub" }, el("td", { colspan: String(WIDTHS.length) }, ...bits)) : null;
  }

  // One need as a table row under its material: the activity in the name column, its amount
  // under the sum, the ready checkbox under the badge it counts into.
  function usageRow(m, u) {
    const cb = el("input", { type: "checkbox", title: "Hotovo" });
    cb.checked = u.is_ready;
    cb.disabled = !mayEdit;
    if (mayEdit) cb.addEventListener("change", async () => {
      try { const j = await api("PATCH", withId(U.needItem, u.need_id), { is_ready: cb.checked }); assignNeed(u, j.need); refreshRow(m); }
      catch (e) { cb.checked = !cb.checked; toast(e.message, true); }
    });
    const tr = el("tr", { class: "cp-mat-usage" + (u.is_ready ? " is-ready" : "") },
      el("td", { class: "cp-mat-usage-name" },
        el("a", { href: withId(U.activityDetail, u.activity_id) + "#materials", class: "cp-usage-act" }, u.activity_title),
        u.note ? el("div", { class: "cp-muted cp-usage-note" }, u.note) : null),
      el("td", null, amountText(u.amount, u.unit || m.unit) || dash()),
      // Sklad, Štítky and Garant belong to the material, not to one activity's need.
      el("td", { colspan: "3" }),
      el("td", { class: "cp-mat-hotovo" }, cb));
    if (mayEdit) tr.append(el("td", { class: "cp-actions" }, actionGroup([
      { label: "✎", title: "Upravit", onClick: () => openUsageEdit(m, u) },
      { label: "✕", title: "Odebrat z aktivity", danger: true, onClick: async () => {
        if (!confirm("Odebrat „" + m.name + "“ z aktivity „" + u.activity_title + "“?")) return;
        try { await api("DELETE", withId(U.needItem, u.need_id)); m.usages = m.usages.filter((x) => x.need_id !== u.need_id); refreshRow(m); toast("Odebráno"); }
        catch (e) { toast(e.message, true); }
      } },
    ])));
    return tr;
  }

  // --- edits -----------------------------------------------------------------
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

  // The material's warehouse thing, chosen in the edit dialog and saved with the rest of
  // it: the current thing by name and box, "Vybrat" over the live things no other material
  // of the camp stands for, "Odpojit" to let go. Nothing is copied from the thing here.
  function linkField(m) {
    let chosen = m.inventory_item || null;
    const text = el("span");
    const pick = segBtn({ label: "Vybrat", onClick: () => openPicker() });
    const drop = segBtn({ label: "Odpojit", onClick: () => { chosen = null; paint(); } });
    const gone = el("div", { class: "cp-field-hint" }, "Věc je vyřazená: je potřeba propojit jinou, nebo odpojit.");
    const paint = () => {
      text.replaceChildren(chosen ? el("span", null, chosen.name + " · " + stock.boxName(chosen)) : dash());
      drop.hidden = !chosen;
      gone.hidden = !(chosen && stock.retired(chosen));
    };
    const open = () => {
      const all = stock.items() || [];
      const taken = new Set(MATS.filter((x) => x.id !== m.id).map((x) => x.inventory_item?.id).filter(Boolean));
      searchPicker({
        title: "Věc ve skladu",
        placeholder: "Hledat věc…",
        items: all.filter((it) => !taken.has(it.id)),
        ...stock.rows(U, all),
        onPick: (it, close) => { close(); chosen = it; paint(); },
        empty: (q) => (q ? "Nic nenalezeno."
          : "Ve skladu není žádná volná věc – na každou odkazuje nejvýš jeden materiál akce."),
      });
    };
    const openPicker = () => stock.load(U).then(open).catch((e) => toast(e.message, true));
    paint();
    return {
      node: el("div", null, el("div", { class: "cp-thumb-row cp-mat-link-row" }, text, actionGroup([], pick, drop)), gone),
      get: () => (chosen ? chosen.id : null),   // null lets go; an unchanged id is a no-op server-side
    };
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
    const link = linkField(m);

    formModal({
      title: "Upravit materiál",
      pane: el("div", { class: "cp-pane" },
        el("label", { class: "cp-field-label" }, "Název"), name,
        el("label", { class: "cp-field-label" }, "Výchozí jednotka"), unit,
        el("label", { class: "cp-field-label" }, "Sčítání množství napříč aktivitami"), strat,
        el("label", { class: "cp-field-label" }, "Štítky pořízení"), acq.node,
        el("div", { class: "cp-field-hint" },
          "Enter, Tab nebo čárka přidá štítek. Backspace vrátí hotový štítek k editaci. " +
          "Tvar „prefix:hodnota“ se zobrazí jako barevný štítek (např. kup:mefisto)."),
        el("label", { class: "cp-field-label" }, "Garant"), orgGroup.node,
        el("label", { class: "cp-field-label" }, "Poznámka"), note,
        el("label", { class: "cp-field-label" }, "Odkaz"), url,
        el("label", { class: "cp-field-label" }, "Věc ve skladu"), link.node),
      onSubmit: async (close) => {
        const nm = name.value.trim();
        if (!nm) { name.focus(); return; }
        const j = await api("PATCH", withId(U.materialItem, m.id),
          { name: nm, unit: unit.value || null, note: note.value || null, url: url.value || null,
            acquisition_labels: acq.get(), sum_strategy: strat.value, org_ids: orgGroup.get(),
            inventory_item_id: link.get() });
        Object.assign(m, j.material);   // envelope carries no `usages` → m.usages preserved
        close(); renderTable(); toast("Uloženo");   // renderTable re-sorts (name may have changed)
      },
    });
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
    mergePicker({
      title: "Sloučit „" + m.name + "“ do…",
      hint: "U všech aktivit se „" + m.name + "“ nahradí vybraným materiálem a „" + m.name + "“ z katalogu zmizí.",
      items: others, labelOf: (t) => t.name, metaOf: (t) => t.unit,
      url: withId(U.materialMerge, m.id),
      confirmText: (t) => "Sloučit „" + m.name + "“ do „" + t.name + "“? U všech aktivit se „" + m.name + "“ nahradí „" + t.name + "“.",
      successText: (t) => "Sloučeno do „" + t.name + "“",
    });
  }

  // arriving from an activity's material list (#material-<id>): open that row, scroll to it,
  // and flash it. Added to `expanded` before buildShell so it renders open from the start.
  const hashMatch = /^#material-(\d+)$/.exec(location.hash);
  const hashId = hashMatch ? Number(hashMatch[1]) : null;
  if (hashId != null) expanded.add(hashId);
  else readHash();   // otherwise the hash carries the filters (#label=…&org=…)

  // React to external hash changes (links / back button); writeHash uses replaceState
  // so it doesn't fire hashchange — no loop. A #material-<id> deep link isn't a filter.
  window.addEventListener("hashchange", () => {
    if (/^#material-\d+$/.test(location.hash)) return;
    readHash();
    buildShell();
  });

  buildShell();

  if (hashId != null) {
    reveal(rowEls.get(hashId)?.firstElementChild);
  }
})();
