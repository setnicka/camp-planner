// Camp Planner: one finished check (/inventory/checks/<id>).
//
// Read-only history: what was observed, grouped by the box it was seen in. The grouping
// comes from the observations themselves, so it keeps telling the truth about that day
// even after things have been moved since.
"use strict";

(function () {
  const mount = document.getElementById("cp-inventory");
  const dataEl = document.getElementById("cp-inventory-data");
  if (!mount || !dataEl) return;

  const { el } = window.cpDom;
  const inv = window.cpInventory;
  const DATA = JSON.parse(dataEl.textContent);
  const U = DATA.urls;
  const check = DATA.check;

  // One span, the dot glued to the date: wrapped, a line must not start with "·".
  const summaryLine = () => el("p", { class: "cp-inv-meta" },
    el("span", { class: "cp-muted" },
      inv.fmtDate(check.completed_at) + " · " + inv.summaryText(check.summary)));

  const nameOf = inv.boxNameOf(DATA.boxes);

  function movedIcon(record) {
    if (!inv.moved(record)) return null;
    return el("span", { class: "cp-inv-hist-moved", "data-cp-hint": "",
      title: "přesunuto sem " + inv.fromLabel(record, nameOf) }, "⤳");
  }

  // The page shows a past state and one glyph; both said once, above the tiles.
  function legend() {
    if (!DATA.groups.length) return null;
    const anyMoved = DATA.groups.some((g) => g.records.some(inv.moved));
    return el("p", { class: "cp-inv-list-hint cp-field-hint" },
      "Počty a krabice jsou stav zjištěný při inventuře"
      + (anyMoved ? ", ⤳\u00a0značí přesunutou věc (klik ukáže odkud)." : "."));
  }

  // The same tile language as the overview map, just read-only: always unfolded,
  // nothing to click but the box name.
  function group(g) {
    const rows = g.records.map((record) => {
      return el("li", null,
        el("span", { class: "cp-inv-peek-name" }, g.names[record.item_id]),
        movedIcon(record),
        record.discarded ? inv.badge("vyřazeno") : inv.amountEl(record.count, record.unit),
        record.note ? el("div", { class: "cp-inv-rec-note cp-muted" }, record.note) : null);
    });
    return el("div", { class: "cp-inv-tile" + (g.box?.virtual ? " cp-inv-tile-virtual" : "") },
      el("div", { class: "cp-inv-tile-head" },
        g.box
          ? el("a", { class: "cp-inv-tile-name", href: inv.boxUrl(U, g.box.id) }, g.box.name)
          : el("span", { class: "cp-inv-tile-name" }, "(smazaná krabice)")),
      el("ul", { class: "cp-inv-tile-items" }, ...rows));
  }

  // By shelf, like the overview map. A deleted box stands nowhere, so it ends up with the
  // boxes that have no location.
  const sections = inv.locationSections(inv.groupByLocation(DATA.groups), (groups) =>
    el("div", { class: "cp-inv-tiles cp-inv-check-tiles" }, ...groups.map(group)));

  mount.replaceChildren(
    el("div", { class: "cp-inv-head" },
      el("h1", null, inv.checkTitle(check)),
      summaryLine(),
      legend()),
    ...(sections.length ? sections
      : [el("p", { class: "cp-muted" }, "V této inventuře nikdo nic nezapsal.")]));
})();
