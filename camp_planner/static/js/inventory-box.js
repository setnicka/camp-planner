// Camp Planner: one box (/inventory/boxes/<id>).
//
// This is where the checking happens: the observation controls live here (the overview
// only revives a retired thing the check has found). Each control is one api call whose
// response is the whole box again, so several people walking the same shelves see each
// other's work. An item has no page: its row opens the shared modal.
"use strict";

(function () {
  const mount = document.getElementById("cp-inventory");
  const dataEl = document.getElementById("cp-inventory-data");
  if (!mount || !dataEl) return;

  const { el, api, amountList, amountText, czechKey, formModal, searchPicker, toast, toastNext, withId,
          reveal } = window.cpDom;
  const inv = window.cpInventory;
  const DATA = JSON.parse(dataEl.textContent);
  const U = DATA.urls;
  const mayEdit = DATA.may_edit;

  let state = DATA.state;                 // {box, items, records, checked, total, active_check}
  // Every answer this page gets carries the check, so somebody else starting or
  // finishing one arrives with the next write instead of on the next reload.
  const checking = () => !!state.active_check;
  let showHistory = false;
  let history = null;                     // finished checks {checks, records, seen}, on demand
  let filter = "";                        // the box's own search, raw as typed
  let hideChecked = false;                // during a check: list only what is still to do

  // Records by item, so the rows look them up instead of scanning the list per control.
  const indexRecords = () => new Map(state.records.map((r) => [r.item_id, r]));
  let byItem = indexRecords();
  const recordOf = (itemId) => byItem.get(itemId) || null;
  const nameOf = inv.boxNameOf(DATA.boxes);
  const boxName = (id) => nameOf(id) || "jiné krabice";
  const boxLink = (id) => inv.boxLink(U, id, nameOf);
  // Why the Historie button is greyed out, and the answer if the box's contents changed
  // under us between the page load and the click.
  const NO_HISTORY = "Věci v této krabici zatím žádná dokončená inventura nezkontrolovala.";

  // --- writing observations ---------------------------------------------------

  const reload = () => apply(api("GET", withId(U.boxState, state.box.id)));
  const write = inv.latest((answer) => { state = answer.state; byItem = indexRecords(); render(); },
                           reload);

  // The row's strip dims until the answer is in: a tap on a slow connection must show it
  // landed, or it gets a second one. The re-render drops the mark; an error leaves the
  // strip where it was, so the mark comes off by hand.
  async function apply(promise, itemId) {
    const strip = itemId == null ? null
      : mount.querySelector(`[data-item="${itemId}"] .cp-inv-row-actions`);
    strip?.classList.add("cp-inv-saving");
    try {
      return await write(promise.catch((e) => {
        // A refused write means the page's own picture is stale, most often because the
        // check ended under it on another phone: fetch the state before the toast lands.
        if (e.status === 400 || e.status === 409) reload();
        throw e;
      }));
    } finally { strip?.classList.remove("cp-inv-saving"); }
  }

  const save = (itemId, body) =>
    apply(api("PUT", inv.recordUrl(U, state.box.id, itemId), body), itemId);

  function forget(itemId, question) {
    if (question && !window.confirm(question)) return;
    apply(api("DELETE", inv.recordUrl(U, state.box.id, itemId)), itemId);
  }

  // --- history columns (the finished checks, fetched on demand) -----------------

  // Set for the one redraw that brings the columns in, so only that one animates them:
  // every observation redraws the list too, and moving columns there would be noise.
  let histEnter = false;

  async function loadHistory() {
    try {
      const j = await api("GET", withId(U.boxHistory, state.box.id));
      // Each item's records keyed by check id, built once: the columns redraw often.
      const seen = new Map();
      for (const r of j.records) {
        if (!seen.has(r.item_id)) seen.set(r.item_id, new Map());
        seen.get(r.item_id).set(r.check_id, r);
      }
      history = { checks: j.checks, records: j.records, seen };
      showHistory = true;
      histEnter = true;
      render();
    } catch (e) { toast(e.message, true); }
  }

  // The last few finished checks that said anything about the things listed here. A phone
  // gets three columns: with five the names lose their floor width and the count is a long
  // scroll away.
  const narrow = window.matchMedia("(max-width: 40rem)");
  const histCap = () => (narrow.matches ? 3 : 5);
  narrow.addEventListener("change", () => { if (showHistory || state.active_check?.camp) render(); });

  const NONE = new Map();
  function histColumns() {
    if (!showHistory || !history) return null;
    const checks = history.checks.filter(
      (c) => state.items.some((i) => (history.seen.get(i.id) || NONE).has(c.id))).slice(-histCap());
    return { checks, at: new Map(checks.map((c, i) => [c.id, i])) };
  }

  // Grid column 1 is the name; history cells fill 2..n+1 (amount and buttons count from
  // the end, matching the inline template render() builds). --i drives the slide-in delay.
  const colStyle = (i) => `grid-column: ${2 + i}; --i: ${i}`;

  // What the check's camp needed of the things here, when the check follows up on one:
  // null when there is no camp or nothing listed was asked for, else the amounts by thing
  // and whether they get a column of their own. A phone has none to spare, so there the
  // number rides under the amount (CSS), naming the camp in the line itself.
  function takenFor(shown) {
    if (!checking() || !state.active_check.camp) return null;
    const at = new Map(state.taken.map((t) => [t.item_id, t]));
    if (!shown.some((i) => at.has(i.id))) return null;
    const camp = state.active_check.camp;
    return { at, column: !narrow.matches, camp: camp.name, campId: camp.id };
  }

  function takenCell(item, tk) {
    const t = tk?.at.get(item.id);
    // The column keeps its place in rows the camp asked nothing of: an empty cell, so the
    // tint and the rules run down the whole list instead of breaking at every gap.
    if (!t) return tk?.column ? el("span", { class: "cp-inv-taken" }) : null;
    const text = amountList(t.totals);
    // Under the amount (a phone) the number has no head above it, so the line spells the
    // whole label out, camp included.
    return el("span", { class: "cp-inv-taken cp-muted", "data-cp-hint": "",
      title: `Akce „${tk.camp}“ potřebovala ${text} (`
        + inv.countLabel(t.activities, "aktivita", "aktivity", "aktivit") + ")" },
      tk.column ? text : `Použito na ${tk.camp}: ${text}`);
  }

  function takenHead(tk) {
    if (!tk?.column) return null;
    const link = DATA.camp_materials?.camp_id === tk.campId ? DATA.camp_materials.url : null;
    // The hint sits on the label alone: it swallows the tap, and the camp may be a link.
    return el("span", { class: "cp-inv-taken-head" },
      el("span", { "data-cp-hint": "", title: "Kolik bylo potřeba na akci " + tk.camp }, "Použito na"),
      el("br"), link ? el("a", { href: link }, tk.camp) : tk.camp);
  }

  // "3 ks → " and "5 ks", split so a row can mute the old amount; no amount shows the
  // empty glyph.
  const changeText = (from, to) =>
    [(amountText(from.count, from.unit) || "—") + " → ", amountText(to.count, to.unit) || "—"];

  // One item's outcome in one finished check as a compact symbol; the tooltip carries
  // the details. `prev` is the item's previous recorded state, the best guess for what
  // the count was before (counts, unlike boxes, have no creation-time snapshot).
  function histCell(r, prev, i, n) {
    // A button, so the keyboard reaches it.
    const cell = (cls, title, ...kids) => el("button",
      { type: "button", "data-cp-hint": "",
        class: "cp-inv-hist-cell " + cls + (i === n - 1 ? " cp-inv-hist-last" : ""),
        style: colStyle(i), title },
      ...kids);
    if (!r) return cell("cp-dim", "v této inventuře nekontrolováno", "—");
    const withNote = (base) => (r.note ? base + " · poznámka: " + r.note : base);
    const now = amountText(r.count, r.unit) || "—";
    if (r.discarded) return cell("cp-inv-hist-gone", withNote("vyřazeno"), "✕");
    const delta = prev && !prev.discarded
      && (prev.count !== r.count || prev.unit !== r.unit)
      ? changeText(prev, r).join("") : null;
    const moved = inv.moved(r);
    const deltaEl = () => (delta ? el("span", { class: "cp-inv-hist-adjust" }, delta) : null);
    if (r.box_id !== state.box.id) {
      // No name: the box of the record was deleted after the check (box_id SET NULL).
      const name = nameOf(r.box_id);
      const base = moved
        ? "přesunuto " + inv.fromLabel(r, nameOf)
          + (name ? " do „" + name + "“" : " do smazané krabice")
        : "tehdy " + (name ? "v krabici „" + name + "“" : "ve smazané krabici");
      return cell("cp-inv-hist-moved",
        withNote(delta ? base + " · " + delta : base), "⤳", deltaEl());
    }
    if (moved) {
      return cell("cp-inv-hist-moved",
        withNote("přesunuto sem " + inv.fromLabel(r, nameOf) + " · " + (delta || now)),
        "⤳", deltaEl());
    }
    if (delta) return cell("cp-inv-hist-adjust", withNote(delta), delta);
    return cell("cp-inv-hist-ok", withNote("beze změny (" + now + ")"), "✓");
  }

  // The item's cells for the shown columns; `prev` walks every finished check, so the
  // comparison base is right even for checks older than the shown ones.
  function histCells(item, hist) {
    if (!hist) return [];
    const seen = history.seen.get(item.id) || NONE;
    const out = [];
    let prev = null;
    for (const c of history.checks) {
      const r = seen.get(c.id) || null;
      if (hist.at.has(c.id)) out.push(histCell(r, prev, hist.at.get(c.id), hist.checks.length));
      if (r) prev = r;
    }
    return out;
  }

  // Dated heads, the check's name in the tooltip; the camp column is headed by the camp.
  function headsRow(hist, tk) {
    return el("li", { class: "cp-inv-row cp-inv-hist-heads" },
      ...(hist ? hist.checks.map((c, i) => el("a", {
        class: "cp-inv-hist-head"
          + (i === hist.checks.length - 1 ? " cp-inv-hist-last" : ""),
        style: colStyle(i),
        href: inv.checkUrl(U, c.id),
        title: inv.checkTitle(c),
      }, inv.fmtDay(c.completed_at))) : []),
      takenHead(tk));
  }

  const AMOUNT_HINT = "Prázdný počet znamená „máme, nepočítáno“. Slovní množství patří "
    + "do jednotky („hodně“, „nekonečno“).";

  // onSave answers whether it saved (see cpInventory.latest): a write that failed has
  // already toasted, and the dialog stays open with the number still in it.
  function amountModal(title, count, unit, onSave) {
    const fields = inv.amountFields(count, unit);
    formModal({
      title,
      pane: el("div", { class: "cp-pane" }, fields.pane,
        el("p", { class: "cp-field-hint" }, AMOUNT_HINT)),
      onSubmit: async (close) => {
        if (await onSave(fields.read())) close();
      },
    });
    fields.focus();
  }

  function editAmount(item) {
    const record = recordOf(item.id);
    const start = record && !record.discarded ? record : item;
    amountModal("Jiný počet – " + item.name, start.count, start.unit,
      (amount) => save(item.id, { discarded: false, ...amount }));
  }

  // Outside a check the count is what gets corrected most often, and the item modal is
  // a lot of dialog (fields, photos) for one number.
  function editItemAmount(item) {
    // Through the same runner as an observation write, so the fresh box state that
    // follows the patch cannot land out of order with one.
    amountModal("Počet – " + item.name, item.count, item.unit, (amount) =>
      apply(api("PATCH", withId(U.itemItem, item.id), amount)
        .then(() => api("GET", withId(U.boxState, state.box.id))), item.id));
  }

  function moveItem(item) {
    inv.boxPicker({
      title: "Přesunout – " + item.name,
      hint: "Kam věc doopravdy patří. Přesune se až dokončením inventury.",
      boxes: DATA.boxes.filter((b) => b.id !== (recordOf(item.id)?.box_id ?? item.box_id)),
      onPick: (box, close) => { close(); save(item.id, { discarded: false, box_id: box.id }); },
    });
  }

  // "Přesunout sem" searches the whole warehouse, retired things included: finding one is
  // exactly how it comes back.
  function moveHere() {
    const here = new Set(state.items.map((i) => i.id));
    searchPicker({
      title: "Přesunout sem",
      hint: "Věc, která leží v této krabici, ale evidujeme ji jinde.",
      items: DATA.all_items.filter((i) => !here.has(i.id)),
      labelOf: (i) => i.name,
      metaOf: (i) => (i.discarded_at ? "vyřazeno" : boxName(i.box_id)),
      onPick: (item, close) => { close(); save(item.id, { discarded: false, box_id: state.box.id }); },
    });
  }

  function noteField(item) {
    const record = recordOf(item.id);
    if (!record) return null;   // nothing to attach a note to; it must not create a record
    // A moved-away row carries only "zrušit přesun"; its note belongs to the target box.
    if (inv.recordState(item, record, state.box.id) === "moved-away") return null;
    const input = el("input", { type: "text", class: "cp-inv-note",
                                "data-focus-key": "note-" + item.id,
                                value: record.note || "",
                                placeholder: "Poznámka…",
                                title: "Poznámka ke stavu, při dokončení se připojí k poznámce věci" });
    input.addEventListener("change", () => save(item.id, { note: input.value.trim() || null }));
    return input;
  }

  // --- rows -------------------------------------------------------------------

  // A live record whose count or unit differs from the item's is an adjustment: the
  // "jiný počet" segment owns that state and the amount column shows what changed.
  const isAdjusted = (item, record, st) => !!record && !record.discarded
    && st !== "moved-away" && (record.count !== item.count || record.unit !== item.unit);

  // The confirm spells out what it throws away: the observation may be a colleague's,
  // written while this page was already open.
  function recordDesc(r) {
    const parts = [r.discarded ? "vyřazeno" : (amountText(r.count, r.unit) || "bez počtu")];
    if (r.note) parts.push("pozn.: " + r.note);
    return "„" + parts.join(" · ") + "“";
  }
  const forgetQuestion = (verb, record) =>
    `Zrušit ${verb} a vrátit věc mezi nezkontrolované? Zahodí se zápis ${recordDesc(record)}.`;

  function controls(item) {
    const record = recordOf(item.id);
    const st = inv.recordState(item, record, state.box.id);
    if (st === "moved-away") {
      // The only thing to do here is take the move back; everything else belongs to the
      // box it went to. Cancelling drops the whole observation, so the item is unchecked
      // again rather than silently "checked".
      return el("div", { class: "cp-inv-moved" },
        el("span", null, "→ ", boxLink(record.box_id)),
        el("button", { type: "button", class: "cp-mini",
                       onclick: () => forget(item.id, forgetQuestion("přesun", record)) }, "zrušit přesun"));
    }
    const adjusted = isAdjusted(item, record, st);
    // A thing the check is bringing back from the discarded shelf cannot also be retired
    // by it: the master row is retired already, so the verdict would write an observation
    // that completion ignores. Taking the find back is what "zrušit vrácení" is for.
    const reviving = st === "moved-in" && !!item.discarded_at;
    // Verdicts first, in order of how often they are tapped, the move after them and the
    // reset last, which is the state "nezkontrolováno" while there is no record and the
    // way back to it once there is one.
    const verb = reviving ? "vrácení" : st === "moved-in" ? "přesun" : "kontrolu";
    const reset = !record
      ? { label: "nezkontrolováno", active: true, onClick: () => {}, kind: "off" }
      : { label: "zrušit " + verb, kind: "off",
          onClick: () => forget(item.id, forgetQuestion(verb, record)) };
    const verdicts = [
      { label: "stav sedí", kind: "ok",
        active: (st === "here" || st === "moved-in") && !adjusted,
        onClick: () => save(item.id, { discarded: false, count: item.count, unit: item.unit }) },
      { label: "jiný počet", kind: "adjust", active: adjusted,
        onClick: () => editAmount(item) },
      reviving ? null : { label: "vyřadit", kind: "gone", active: st === "discarded",
        onClick: () => save(item.id, { discarded: true }) },
      { label: "přesunout", kind: "move", onClick: () => moveItem(item) },
      reset,
    ];
    // Every write redraws the row: the focus key lets the keyboard carry on from the same
    // control (keepFocus) instead of from the top of the page.
    return inv.actionGroup(verdicts.map((d) => d && { ...d, focusKey: `seg-${item.id}-${d.kind}` }),
      noteField(item));
  }

  function openItem(item) {
    inv.itemModal({
      item, boxes: DATA.boxes, urls: U, mayEdit,
      photosEnabled: DATA.photos_enabled,
      // While a check runs, counts change through its controls only.
      lockAmounts: checking(),
      onChange: () => reload(),
    });
  }

  // During a check the move is an observation, so ✕ only erases a mistaken entry.
  // Viewers get ⓘ, the same dialog read-only (where the photos live).
  function rowButtons(item) {
    if (!mayEdit) return [inv.actionGroup([{ label: "ⓘ", title: "Detail", onClick: () => openItem(item) }])];
    return [inv.actionGroup([
      { label: "✎", title: "Upravit", onClick: () => openItem(item) },
      checking() ? null : { label: "⤳", title: "Přesunout do jiné krabice",
        onClick: () => inv.movePicker({ item, boxes: DATA.boxes, urls: U, onChange: () => reload() }) },
      { label: "✕", danger: true,
        title: checking() ? "Smazat omylem založenou věc" : "Odebrat (vyřadit / smazat)",
        onClick: () => inv.removeModal({ item, urls: U, checking: checking(), onChange: () => reload() }) },
    ])];
  }

  // The amount doubles as the button that fixes it; during a check the count belongs to
  // the observation controls, so there it is plain text again.
  function amountControl(item) {
    if (!mayEdit || checking()) return inv.amountEl(item.count, item.unit);
    const text = amountText(item.count, item.unit);
    return el("button", { type: "button", title: "Upravit počet",
      class: "cp-inv-amount cp-inv-amount-btn" + (text ? "" : " cp-dim"),
      "aria-label": "Upravit počet – " + item.name,
      onclick: () => editItemAmount(item) }, text || "—");
  }

  // What the row says beside the name: what the check is doing to the thing, and failing
  // that its master state. A revived row does not add "vyřazeno", because "vrací se
  // z vyřazených" already says it; present tense, because nothing is written until the
  // check is completed.
  function marks(item, st) {
    if (st === "moved-in") {
      return [item.discarded_at
        ? inv.badge("vrací se z vyřazených")
        : inv.badge(["← ", boxLink(item.box_id)], "přesunuto sem")];
    }
    return [item.discarded_at ? inv.badge("vyřazeno") : null,
            st === "discarded" ? inv.badge("vyřazuje se") : null];
  }

  function row(item, hist, tk) {
    const record = recordOf(item.id);
    const st = inv.recordState(item, record, state.box.id);
    // A live record that differs shows the change; otherwise the record agrees with the
    // item (or is not about this box), so the item's own amount is the one to show.
    const change = isAdjusted(item, record, st) ? changeText(item, record) : null;
    const amountCell = change
      ? el("span", { class: "cp-inv-amount" }, el("span", { class: "cp-muted" }, change[0]), change[1])
      : amountControl(item);
    const node = el("li", { class: "cp-inv-row cp-inv-row-" + st, "data-item": item.id },
      el("div", { class: "cp-inv-row-main" },
        inv.itemLabel(item, U, ...marks(item, st))),
      ...histCells(item, hist),
      takenCell(item, tk),
      amountCell,
      el("span", { class: "cp-inv-row-btns" }, ...rowButtons(item)),
      inv.itemSubline(item),
      mayEdit && checking() ? el("div", { class: "cp-inv-row-actions" },
        controls(item)) : null);
    return node;
  }

  // --- box header + item creation ----------------------------------------------

  function editBox() {
    inv.boxModal({
      title: "Upravit krabici",
      box: state.box,
      locations: DATA.boxes.map((b) => b.location),
      onSubmit: async (payload, close) => {
        await api("PATCH", withId(U.boxItem, state.box.id), payload);
        close();
        toastNext("Uloženo.");
        location.reload();
      },
    });
  }

  async function deleteBox() {
    const warn = DATA.in_history
      ? " Figuruje v dokončených inventurách – její záznamy tam zůstanou, "
        + "ale jen jako „(smazaná krabice)“."
      : "";
    if (!window.confirm(`Smazat krabici „${state.box.name}“?${warn}`)) return;
    try {
      await api("DELETE", withId(U.boxItem, state.box.id));
      location.href = U.overview;
    } catch (e) { toast(e.message, true); }
  }

  function addItem() {
    const hint = el("p", { class: "cp-field-hint", hidden: true });
    const fields = inv.itemFields({}, {
      afterName: hint,
      afterAmount: checking() ? el("div", { class: "cp-field-hint" },
        "Probíhá inventura: věc se rovnou zapíše jako zkontrolovaná se zadaným počtem.") : null,
      afterNote: DATA.photos_enabled
        ? el("div", { class: "cp-field-hint" }, "Fotky se přidávají po založení věci, v jejím dialogu (✎).")
        : null,
    });
    // Soft duplicate check: the warehouse may hold two things of the same name, we only say
    // so. From three letters up: one or two match half the warehouse.
    const names = DATA.all_items.map((i) => [czechKey(i.name), i]);   // folded once
    fields.name.addEventListener("input", () => {
      const q = czechKey(fields.name.value.trim());
      const hits = [];
      if (q.length >= 3) {
        for (const [folded, i] of names) {
          if (folded.includes(q) && hits.push(i) === 3) break;
        }
      }
      const where = (i) => i.name + " (" + (i.discarded_at ? "vyřazeno" : boxName(i.box_id)) + ")";
      hint.textContent = !hits.length ? ""
        : (hits.length === 1 ? "Ve skladu už je podobná věc: " : "Ve skladu už jsou podobné věci: ")
          + hits.map(where).join(", ");
      hint.hidden = !hits.length;
    });
    formModal({
      title: "Přidat novou věc do krabice „" + state.box.name + "“",
      pane: el("div", { class: "cp-pane" }, ...fields.nodes),
      okLabel: "Přidat",
      // The page stays: a reload would lose the search, the history columns and the
      // switch, and the new row is easier to spot when it lights up in place.
      onSubmit: async (close) => {
        const item = (await api("POST", U.items, { ...fields.read(), box_id: state.box.id })).item;
        close();
        toast("Přidáno.");
        DATA.all_items.push(item);   // the next duplicate hint and "Přesunout sem" know it
        await reload();
        reveal(mount.querySelector(`[data-item="${item.id}"]`));
      },
    });
  }

  // One line per block above the list: a phone shows barely three item rows as it is.
  function header(histEmpty) {
    return el("div", { class: "cp-inv-head" },
      // Crumbs in the heading, since a host template may not render our nav. The space
      // before the badge is the line's only break opportunity there.
      el("h1", null,
        el("span", { class: "cp-inv-crumbs" },
          el("a", { class: "cp-inv-crumb", href: U.overview }, "Sklad"), " › ",
          ...(state.box.location ? [
            el("a", { class: "cp-inv-crumb", href: inv.locationUrl(U, state.box.location) },
              state.box.location), " › "] : [])),
        state.box.name,
        ...(state.box.virtual
          ? [" ", el("span", { class: "cp-inv-badge" }, "virtuální")] : [])),
      state.box.note ? el("p", { class: "cp-inv-note-text" }, state.box.note) : null,
      el("div", { class: "cp-inv-head-actions" },
        mayEdit ? inv.actionGroup([
          // The heading right above says what these act on.
          { label: "Upravit", onClick: editBox },
          // The reason comes from the predicate the server refuses on (delete_blocked).
          { label: "Smazat", danger: true, onClick: deleteBox, disabled: DATA.delete_blocked },
        ]) : null,
        // Its own group, so a narrow screen wraps between the groups and neither of
        // them breaks apart.
        inv.actionGroup([
          {
            label: showHistory ? "Skrýt historii" : "Historie",
            onClick: () => {
              if (showHistory) { showHistory = false; render(); } else loadHistory();
            },
            disabled: DATA.has_history ? null : NO_HISTORY,
          },
        ]),
        hideCheckedBox()),
      histEmpty ? el("p", { class: "cp-muted" }, NO_HISTORY) : null);
  }

  // --- the box's own search ------------------------------------------------------
  //
  // Below this many things the field is more chrome than help.
  const FILTER_FROM = 8;
  // Below the threshold there is no field to clear, so a query left over from before the
  // box shrank must not keep hiding things.
  const filterable = () => state.items.length >= FILTER_FROM;

  const unchecked = (item) => inv.recordState(item, recordOf(item.id), state.box.id) === "unchecked";
  const hiding = () => checking() && hideChecked;

  function matching() {
    const q = filterable() ? czechKey(filter.trim()) : "";
    return state.items.filter((i) => (!hiding() || unchecked(i))
      && (!q || inv.searchText(i, i.note).includes(q)));
  }

  // Built once and kept: a redraw per keystroke must not replace the field being typed in.
  const filterBar = el("div", { class: "cp-inv-toolbar cp-inv-box-filter" },
    inv.searchField({
      placeholder: "Hledat v krabici…",
      ariaLabel: "Hledat věc v této krabici",
      onInput: (value) => { filter = value; render(); },
    }).wrap);

  // During a check: the switch that turns the box into a shrinking to-do list, a checked
  // row leaving with the redraw that lights it. A checkbox, not a button: it is a view
  // state, and next to the box actions a lit button would read as one more of them.
  function hideCheckedBox() {
    if (!checking() || !state.items.length) return null;
    const box = el("input", { type: "checkbox", checked: hideChecked });
    box.addEventListener("change", () => { hideChecked = box.checked; render(); });
    return el("label", { class: "cp-inv-hide-done" }, box, "skrýt zkontrolované");
  }

  // The icons of the list, said once above it. Under a check the legend rides in the
  // check banner instead.
  function listHint() {
    if (!state.items.length || !mayEdit || checking()) return null;
    return el("p", { class: "cp-inv-list-hint cp-field-hint" },
      "✎\u00a0upraví věc, kliknutí na počet opraví jen počet, "
      + "⤳\u00a0přesune věc do jiné krabice, ✕\u00a0ji vyřadí nebo smaže.");
  }

  // The name links to the checks page, where finishing and cancelling live. The other
  // banners show whole-warehouse progress, so this one says its scope; and it carries the
  // list legend for the check, since it is the check's own place on the page.
  function checkBanner(tk) {
    if (!checking()) return null;
    const bar = inv.checkBar(el("a", { href: U.checksPage }, inv.checkTitle(state.active_check)),
      state.checked, state.total, "v této krabici",
      mayEdit && state.items.length ? el("span", { class: "cp-inv-bar-hint cp-field-hint" },
        "✎\u00a0upraví název a podrobnosti, počty a přesuny jdou jen přes inventuru. "
        + "✕\u00a0smaže omylem založenou věc. Změny se ukládají hned.") : null,
      // The column's head is a tooltip away on a touch screen, and a phone has no head.
      tk ? el("span", { class: "cp-inv-bar-hint cp-field-hint" },
        (tk.column ? "Sloupec" : "Štítek u počtu")
        + ` „Použito na ${tk.camp}“ ukazuje, kolik které věci akce potřebovala.`) : null);
    bar.classList.add("cp-inv-bar-box");   // shares the list's right edge
    return bar;
  }

  // Show the fade only while columns are still hidden past the right edge. Reads
  // geometry and nothing else, because it runs on every scroll event.
  function refreshHistFade() {
    const list = mount.querySelector(".cp-inv-items-hist");
    if (!list) return;
    list.parentElement.classList.toggle("cp-inv-more-right",
      list.scrollLeft + list.clientWidth < list.scrollWidth - 1);
  }

  // Layout reads (the camp column's place, the width the strips are pinned to), kept out
  // of the scroll handler: they only change on a redraw or a resize.
  function measureList() {
    const list = mount.querySelector(".cp-inv-items-detail");
    if (!list) return;
    const head = list.querySelector(".cp-inv-taken-head");
    if (head) {
      list.style.setProperty("--taken-l", head.offsetLeft + "px");
      list.style.setProperty("--taken-w", head.offsetWidth + "px");
    }
    if (!list.classList.contains("cp-inv-items-hist")) return;
    list.style.setProperty("--list-w", list.clientWidth + "px");
    refreshHistFade();
  }
  window.addEventListener("resize", measureList);

  // The redraw drops every node. Whoever already clicked into the next note field (its
  // blur saved the previous one) must not lose it: fields and verdicts carry a focus key
  // and the redraw hands focus (and a field's draft text and caret) back through the
  // function returned here.
  function keepFocus() {
    const live = document.activeElement;
    const key = live?.dataset?.focusKey;
    if (!key) return () => {};
    const field = live.tagName === "INPUT";
    const draft = field ? live.value : null;
    const caret = field && live.selectionStart !== null ? [live.selectionStart, live.selectionEnd] : null;
    return () => {
      const again = mount.querySelector(`[data-focus-key="${CSS.escape(key)}"]`);
      if (!again) return;
      if (field) again.value = draft;
      again.focus();
      if (caret) again.setSelectionRange(caret[0], caret[1]);
    };
  }

  const parts = { banner: el("div"), header: el("div"), filter: filterBar, hint: el("div"),
                  list: el("div"), add: el("div") };
  mount.replaceChildren(...Object.values(parts));

  function render() {
    const restoreFocus = keepFocus();
    const scroller = mount.querySelector(".cp-inv-items-hist");
    const scrolled = scroller ? scroller.scrollLeft : 0;
    let hist = histColumns();
    const histEmpty = hist && !hist.checks.length;
    if (histEmpty) hist = null;
    const shown = matching();
    const tk = takenFor(shown);
    // The template grows a column per shown check and one for the camp; the amount and
    // the buttons count from the end (CSS), so they stay put.
    const extra = (hist ? hist.checks.length : 0) + (tk?.column ? 1 : 0);
    const list = el("ul", { class: "cp-inv-items cp-inv-items-detail"
                   + (hist ? " cp-inv-items-hist" : "") + (hist && histEnter ? " cp-inv-hist-enter" : "")
                   + (tk?.column ? " cp-inv-taken-col" : ""),
                 style: extra ? "grid-template-columns: minmax(min(11rem, 55vw), 1fr)"
                   + ` repeat(${extra}, max-content) max-content max-content` : null },
      ...(hist || tk?.column ? [headsRow(hist, tk)] : []),
      ...(shown.length ? shown.map((i) => row(i, hist, tk))
                       : [el("li", { class: "cp-muted" },
                           !state.items.length ? "Krabice je prázdná."
                             : hiding() && !state.items.some(unchecked) ? "Vše zkontrolováno."
                             : "Nic nenalezeno.")]));
    if (hist) list.addEventListener("scroll", refreshHistFade, { passive: true });
    const put = (part, ...nodes) => part.replaceChildren(...nodes.filter(Boolean));
    put(parts.banner, checkBanner(tk));
    put(parts.header, header(histEmpty));
    parts.filter.hidden = !filterable();
    put(parts.hint, listHint());
    put(parts.list, hist ? el("div", { class: "cp-inv-hist-wrap" }, list) : list);
    put(parts.add, mayEdit ? el("div", { class: "cp-inv-add-row" },
      checking() ? el("button", { type: "button", class: "cp-add", onclick: moveHere },
        "↝ Přesunout existující věc z jiné krabice") : null,
      el("button", { type: "button", class: "cp-add", onclick: addItem },
        "+ Přidat novou věc")) : null);
    if (hist && scrolled) list.scrollLeft = scrolled;
    histEnter = false;
    measureList();
    restoreFocus();
  }

  render();

  // Arriving from a camp's material (#item-<id>): scroll to the thing and flash it.
  const hashItem = /^#item-(\d+)$/.exec(location.hash);
  if (hashItem) reveal(mount.querySelector(`[data-item="${hashItem[1]}"]`));
})();
