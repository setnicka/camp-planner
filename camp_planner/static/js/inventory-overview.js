// Camp Planner: the warehouse overview (/inventory).
//
// A map of the warehouse: boxes grouped by where they stand, each group a grid of tiles.
// The box name links to its page; a click anywhere else on the tile unfolds a peek.
// Searching narrows the map to matching boxes and unfolds them. Inventory checks live on
// /inventory/checks; a running one shows here as a banner, per-shelf and per-tile progress,
// and a retired thing it has found is revived from the shelf here.
"use strict";

(function () {
  const mount = document.getElementById("cp-inventory");
  const dataEl = document.getElementById("cp-inventory-data");
  if (!mount || !dataEl) return;

  const { el, api, amountText, czechKey, plural, toastNext, withId } = window.cpDom;
  const inv = window.cpInventory;
  const DATA = JSON.parse(dataEl.textContent);
  const U = DATA.urls;
  const mayEdit = DATA.may_edit;

  let query = "";
  let discardedOpen = false;
  const open = new Set();   // unfolded tiles; page-local on purpose, a reload folds the map
  const full = new Set();   // tiles showing past the first PEEK things
  const PEEK = 5;

  function checkBanner() {
    if (!DATA.active_check) return null;
    const totals = inv.sumProgress(DATA.boxes);
    return inv.checkBar(el("a", { href: U.checksPage }, DATA.active_check.name),
      totals.checked, totals.total, null);
  }

  function newBox() {
    inv.boxModal({
      title: "Nová krabice",
      okLabel: "Vytvořit",
      locations: DATA.boxes.map((e) => e.box.location),
      onSubmit: async (payload, close) => {
        const box = (await api("POST", U.boxes, payload)).box;
        close();
        location.href = inv.boxUrl(U, box.id);
      },
    });
  }

  // --- search --------------------------------------------------------------------

  // Folded once, not on every keystroke over every thing in the warehouse. The box's own
  // name counts too, so an empty box is still findable.
  const hay = new Map();       // item id -> folded search text
  const boxHay = new Map();    // box id -> folded name
  function index(entry) {
    boxHay.set(entry.box.id, czechKey(entry.box.name));
    for (const item of entry.items) hay.set(item.id, inv.searchText(item, entry.box.name));
  }
  DATA.boxes.forEach(index);
  for (const item of DATA.discarded) hay.set(item.id, inv.searchText(item));

  const matches = (item) => !query || hay.get(item.id).includes(query);
  const nameHit = (box) => !!query && boxHay.get(box.id).includes(query);
  // A box found by its own name unfolds whole; otherwise the peek narrows to the hits.
  const visible = (entry) =>
    (query && !nameHit(entry.box) ? entry.items.filter(matches) : entry.items);

  // --- the map: location groups of box tiles -----------------------------------

  // DATA.boxes never changes, so the shelves are grouped once.
  const GROUPS = inv.groupByLocation(DATA.boxes);
  const BOXES = DATA.boxes.map((e) => e.box);
  const boxNameOf = inv.boxNameOf(BOXES);

  // The keyboard's place after a redraw: a fold must not drop the focus on the body.
  const refocus = (selector) => mount.querySelector(selector)?.focus();

  function tile(entry, shown) {
    const { box, items, checked, total } = entry;
    const isOpen = query ? true : open.has(box.id);
    const head = el("div", { class: "cp-inv-tile-head" },
      el("span", { class: "cp-inv-caret" }, isOpen ? "▾" : "▸"),
      el("a", { class: "cp-inv-tile-name", href: inv.boxUrl(U, box.id) }, box.name),
      // During a check the progress alone, with the noun kept: its total counts what the
      // check has put here, the listing counts what is filed here, and two different
      // totals side by side read as a mistake.
      el("span", { class: "cp-inv-tile-meta" },
        ...(DATA.active_check && total ? [
          inv.progressEl(checked, total),
          el("span", { class: "cp-muted" }, plural(total, "věc", "věci", "věcí")),
        ] : [el("span", { class: "cp-muted" }, items.length ? inv.things(items.length) : "prázdná")])));
    // Capped, except under a search or for a virtual box (laid out in columns).
    const capped = !query && !box.virtual && !full.has(box.id) && shown.length > PEEK;
    const listed = capped ? shown.slice(0, PEEK) : shown;
    const selector = `[data-box="${box.id}"]`;
    const peek = isOpen ? [
      box.note ? el("div", { class: "cp-inv-tile-note cp-muted" }, box.note) : null,
      shown.length ? el("ul", { class: "cp-inv-tile-items" },
        ...listed.map((i) => {
          const amt = amountText(i.count, i.unit);
          return el("li", null,
            el("span", { class: "cp-inv-peek-name" }, i.name),
            amt ? el("span", { class: "cp-inv-amount cp-muted" }, amt) : null);
        }),
        capped ? el("li", { class: "cp-inv-tile-more" },
          el("button", { type: "button",
                         onclick: () => { full.add(box.id); render(); refocus(selector); } },
            "… a " + inv.countLabel(shown.length - PEEK, "další věc", "další věci", "dalších věcí")))
          : null) : null,
    ] : [];
    const done = DATA.active_check && total && checked >= total;
    const node = el("div", {
      class: "cp-inv-tile" + (box.virtual ? " cp-inv-tile-virtual" : "") + (done ? " cp-inv-tile-done" : ""),
      "aria-expanded": String(isOpen),
      "data-box": box.id,
    }, head, ...peek);
    inv.activatable(node, () => {
      if (query) return;   // a search forces everything open; don't toggle blind state
      if (open.has(box.id)) open.delete(box.id); else open.add(box.id);
      render();
      refocus(selector);
    });
    return node;
  }

  // Where the running check has found this retired thing, or null. Its master row stays
  // retired until completion, exactly like a move stays an observation until then.
  const revivedTo = (item) => DATA.revived[item.id] ?? null;

  // --- keeping the page in step with a write ------------------------------------
  //
  // Patched in place: a reload loses the unfolded tiles and the scroll position (only a
  // refused write reloads).

  // One box changed. An observation write answers with exactly this, and it is the tile's
  // own shape, the item order included (that ordering is the server's).
  function patchBox(state) {
    const entry = DATA.boxes.find((e) => e.box.id === state.box.id);
    if (!entry) return;
    Object.assign(entry, { box: state.box, items: state.items,
                           checked: state.checked, total: state.total });
    index(entry);
  }

  // One series per box: an answer carries only its own box, so a later write into another
  // box must not make it look superseded. A refused write means the page's picture is
  // stale, most often because the check ended on another phone, and only a reload
  // brings the banner and every tile back in step.
  const writers = new Map();
  function write(boxId, promise) {
    if (!writers.has(boxId)) writers.set(boxId, inv.latest((answer) => { patchBox(answer.state); render(); }));
    return writers.get(boxId)(promise.catch((e) => {
      if (e.status === 400 || e.status === 409) {
        toastNext(e.message, true);   // the reload would wipe the toast latest() shows
        location.reload();
      }
      throw e;
    }));
  }

  // The master row moved, so this box's listing has to come from the server; one box is
  // still a fraction of the whole warehouse a reload would fetch.
  const refreshBox = (boxId) => write(boxId, api("GET", withId(U.boxState, boxId)));

  // Bringing a thing back during a check is an observation like any other move (writing
  // the master row would throw away whatever the check already recorded about it), so it
  // is written and taken back the same way: one record, one box state back.
  function writeRevive(item, boxId, revive) {
    write(boxId, api(revive ? "PUT" : "DELETE", inv.recordUrl(U, boxId, item.id),
              revive ? { discarded: false, box_id: boxId } : undefined)
      .then((answer) => {
        if (revive) DATA.revived[item.id] = boxId; else delete DATA.revived[item.id];
        return answer;
      }));
  }

  // A shelf thing was edited (fresh) or is gone (null). A revived one is listed twice, on
  // the shelf and in the tile the check put it in, and the tile's copy is the server's.
  function shelfChanged(item, fresh) {
    const to = revivedTo(item);
    if (fresh) {
      Object.assign(item, fresh);
      hay.set(item.id, inv.searchText(item));
    } else {
      DATA.discarded = DATA.discarded.filter((d) => d.id !== item.id);
      delete DATA.revived[item.id];
    }
    render();
    if (to !== null) refreshBox(to);
  }

  function reviveByObservation(item) {
    inv.boxPicker({
      title: "Vrátit do skladu – " + item.name,
      hint: "Probíhá inventura: zapíše se jako nalezená věc a vrátí se dokončením.",
      boxes: BOXES,
      onPick: (box, close) => { close(); writeRevive(item, box.id, true); },
    });
  }

  // The thing's own dialog, exactly as a box row's ✎ opens it.
  function openItem(item) {
    inv.itemModal({
      item, boxes: BOXES, urls: U, mayEdit,
      photosEnabled: DATA.photos_enabled,
      // As on a box row: while a check runs the amount is the check's to change.
      lockAmounts: !!DATA.active_check,
      // Fires per mutation while the modal is open (photo uploads included).
      onChange: (fresh) => shelfChanged(item, fresh),
    });
  }

  // The row language of a box row, with the one difference that matters here: bringing a
  // thing back is spelled out, because the box page's ⤳ says "to another box" and this
  // is not that. ✕ opens the same warning dialog as in a box, minus the retire option.
  function discardedButtons(item, to) {
    if (!mayEdit) return [inv.actionGroup([{ label: "ⓘ", title: "Detail", onClick: () => openItem(item) }])];
    const back = to !== null
      ? { label: "Zrušit vrácení", title: "Zahodí zápis inventury, věc zůstane vyřazená",
          onClick: () => writeRevive(item, to, false) }
      : { label: "Vrátit do skladu",
          title: DATA.active_check
            ? "Zapíše se jako nalezená věc a vrátí se dokončením inventury"
            : "Vrátit věc do krabice",
          onClick: () => (DATA.active_check
            ? reviveByObservation(item)
            : inv.restorePicker({ item, boxes: BOXES, urls: U,
                onChange: (fresh) => { shelfChanged(item, null); refreshBox(fresh.box_id); } })) };
    return [inv.actionGroup([
      { label: "✎", title: "Upravit", onClick: () => openItem(item) },
      back,
      { label: "✕", danger: true, title: "Smazat včetně fotek a historie inventur",
        onClick: () => inv.removeModal({ item, urls: U, onChange: () => shelfChanged(item, null) }) },
    ])];
  }

  function discardedRow(item) {
    const to = revivedTo(item);
    const node = el("li", { class: "cp-inv-row" },
      // No "vyřazeno" badge: the section heading and the date cell of this very row both
      // say it already. The one badge worth a row here is the box page's "moved in"
      // marker, read from this side.
      inv.itemLabel(item, U, to === null ? null
        : inv.badge(["→ ", inv.boxLink(U, to, boxNameOf)], "inventura ji našla v krabici")),
      inv.amountEl(item.count, item.unit),
      el("span", { class: "cp-muted" },
        "vyřazeno " + inv.fmtDate(item.discarded_at)),
      el("span", { class: "cp-inv-row-btns" }, ...discardedButtons(item, to)),
      inv.itemSubline(item));
    return node;
  }

  function discardedSection() {
    const shown = DATA.discarded.filter(matches);
    if (!shown.length) return null;
    const isOpen = query ? true : discardedOpen;
    // Folded, the rows are not built at all: every keystroke of the search redraws this.
    const body = el("ul", { class: "cp-inv-items" },
      ...(isOpen ? shown.map(discardedRow) : []));
    body.hidden = !isOpen;
    const head = el("div", { class: "cp-inv-box-head",
                             "aria-expanded": String(isOpen) },
      el("span", { class: "cp-inv-caret" }, isOpen ? "▾" : "▸"),
      el("span", { class: "cp-inv-box-name" }, "Vyřazené"),
      el("span", { class: "cp-inv-count cp-muted" }, inv.things(shown.length)));
    inv.activatable(head, () => {
      if (query) return;
      discardedOpen = !discardedOpen;
      render();
      refocus(".cp-inv-box-head");
    });
    return el("section", { class: "cp-inv-discarded" }, head, body);
  }

  // --- page ------------------------------------------------------------------

  const search = inv.searchField({
    placeholder: "Hledat věc nebo krabici…",
    ariaLabel: "Hledat věc nebo krabici",
    onInput: (value) => { query = czechKey(value.trim()); render(); },
  });
  const bannerEl = el("div");
  const boxesEl = el("div");
  const discardedEl = el("div");

  // Only the lists are re-rendered; the search box keeps its node (and the focus with it).
  function render() {
    bannerEl.replaceChildren(...[checkBanner()].filter(Boolean));
    const located = GROUPS
      .map(([loc, entries]) => [loc, entries.map((e) => [e, visible(e)])
        .filter(([e, shown]) => !query || nameHit(e.box) || shown.length)])
      .filter(([, entries]) => entries.length);
    // The shelf's whole progress, not only the boxes a search left showing.
    const subtotal = DATA.active_check && ((entries, loc) => {
      const { checked, total } = inv.sumProgress(GROUPS.find(([l]) => l === loc)[1]);
      return total ? inv.progressEl(checked, total) : null;
    });
    const sections = inv.locationSections(located, (entries) =>
      el("div", { class: "cp-inv-tiles" }, ...entries.map(([e, shown]) => tile(e, shown))),
      { attrs: (loc) => ({ "data-loc": loc }), head: subtotal || undefined });
    const discarded = discardedSection();
    boxesEl.replaceChildren(...(sections.length ? sections : (discarded ? [] : [
      el("p", { class: "cp-muted" }, query ? "Nic nenalezeno." : "Sklad je zatím prázdný."),
    ])));
    discardedEl.replaceChildren(...[discarded].filter(Boolean));
  }

  // A box page's location crumb lands on that shelf.
  function jumpToShelf() {
    const loc = inv.locationOf(location.hash);
    const target = loc !== null && [...boxesEl.querySelectorAll("section")]
      .find((sec) => sec.dataset.loc === loc);
    if (target) target.scrollIntoView({ block: "start" });
  }

  mount.replaceChildren(...[
    bannerEl,
    el("div", { class: "cp-inv-toolbar" },
      search.wrap,
      mayEdit ? el("button", { type: "button", class: "cp-add", onclick: newBox },
        "+ Nová krabice") : null),
    boxesEl,
    discardedEl,
  ].filter(Boolean));
  render();
  jumpToShelf();
})();
