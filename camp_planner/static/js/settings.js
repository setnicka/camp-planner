// Settings page: per-section taxonomy editor (categories / orgs / tags).
//
// Lists are rendered read-only from the JSON embedded in #cp-tax-data. The pencil
// (data-edit-toggle) flips a section into edit mode: rename inline, add rows,
// delete, and — for categories/tags — drag to reorder. "Uložit" PUTs the whole
// desired list to the REST endpoint; the server reconciles and returns the saved
// list (with real ids), then we re-render read-only and show a flash.
"use strict";

(function () {
  const dataEl = document.getElementById("cp-tax-data");
  if (!dataEl) return;
  const { el, csrf, swatch } = window.cpDom;
  const DATA = JSON.parse(dataEl.textContent);
  const KIND_LABEL = Object.fromEntries(DATA.tag_kinds); // value -> czech label

  const TYPES = {
    categories: {
      sortable: true, empty: "Žádné kategorie.",
      cols: [
        { key: "key", label: "Klíč", type: "text" },
        { key: "label", label: "Název", type: "text" },
        { key: "color", label: "Barva", type: "color" },
      ],
    },
    orgs: {
      sortable: false, empty: "Žádní orgové.",
      cols: [
        { key: "initials", label: "Iniciály", type: "text" },
        { key: "name", label: "Jméno", type: "text" },
      ],
    },
    tags: {
      sortable: true, empty: "Žádné tagy.",
      cols: [
        { key: "name", label: "Název", type: "text" },
        { key: "kind", label: "Typ hodnoty", type: "select" },
        { key: "pinned", label: "Na nástěnce", type: "checkbox" },
      ],
    },
  };

  document.querySelectorAll("[data-tax]").forEach((section) => {
    const type = section.dataset.tax;
    const cfg = TYPES[type];
    const body = section.querySelector("[data-tax-body]");
    const toggle = section.querySelector("[data-edit-toggle]");
    const flashArea = section.querySelector("[data-tax-flash]");
    let editing = false;
    let dragRow = null;

    // Result message, shown just under this section's heading.
    const flash = (message, isError) => window.cpDom.flash(flashArea, message, isError);

    function buildTable(headings, tbody) {
      const htr = el("tr", null, ...headings.map((h) => el("th", null, h)));
      return el("table", { class: "cp-table" }, el("thead", null, htr), tbody);
    }

    // ---- read-only view ----
    function viewCell(item, col) {
      if (col.type === "color") {
        return el("td", null, swatch(item.color), item.color || "");
      }
      if (col.type === "select") return el("td", null, KIND_LABEL[item[col.key]] || item[col.key] || "");
      if (col.type === "checkbox") return el("td", null, item[col.key] ? "✓" : "");
      return el("td", null, item[col.key] == null ? "" : String(item[col.key]));
    }

    function renderView() {
      editing = false;
      if (toggle) toggle.classList.remove("on");
      const tb = el("tbody");
      if (!DATA[type].length) {
        tb.append(el("tr", null, el("td", { class: "cp-muted", colspan: cfg.cols.length }, cfg.empty)));
      }
      DATA[type].forEach((item) => tb.append(el("tr", null, ...cfg.cols.map((col) => viewCell(item, col)))));
      body.replaceChildren(buildTable(cfg.cols.map((c) => c.label), tb));
    }

    // ---- edit view ----
    function dragHandle(tr) {
      const h = el("span", { class: "cp-drag", title: "Přetáhněte pro změnu pořadí" }, "⠿");
      h.draggable = true;
      h.addEventListener("dragstart", (e) => {
        dragRow = tr; e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text", ""); tr.classList.add("cp-dragging");
      });
      h.addEventListener("dragend", () => { dragRow = null; tr.classList.remove("cp-dragging"); });
      return h;
    }

    function editRow(item) {
      const tr = el("tr");
      if (item.id != null) tr.dataset.id = item.id;
      if (cfg.sortable) tr.append(el("td", { class: "cp-actions" }, dragHandle(tr)));
      cfg.cols.forEach((col) => {
        let field;
        if (col.type === "select") {
          field = el("select", { name: col.key });
          DATA.tag_kinds.forEach(([value, label]) => {
            const opt = el("option", { value }, label);
            if (item[col.key] === value) opt.selected = true;
            field.append(opt);
          });
        } else if (col.type === "checkbox") {
          field = el("input", { type: "checkbox", name: col.key });
          field.checked = !!item[col.key];
        } else {
          field = el("input", { type: col.type === "color" ? "color" : "text", name: col.key, value: item[col.key] || "" });
        }
        tr.append(el("td", null, field));
      });
      const del = el("button", { type: "button", class: "cp-danger" }, "Smazat");
      del.addEventListener("click", () => tr.remove());
      tr.append(el("td", { class: "cp-actions" }, del));
      return tr;
    }

    function collect() {
      return [...body.querySelectorAll("tbody tr")].map((tr) => {
        const item = {};
        if (tr.dataset.id) item.id = parseInt(tr.dataset.id, 10);
        cfg.cols.forEach((col) => {
          const f = tr.querySelector('[name="' + col.key + '"]');
          item[col.key] = col.type === "checkbox" ? f.checked : f.value;
        });
        return item;
      });
    }

    async function save(btn) {
      btn.disabled = true;
      try {
        const resp = await fetch(DATA.urls[type], {
          method: "PUT",
          headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
          body: JSON.stringify({ items: collect() }),
        });
        const json = await resp.json().catch(() => ({}));
        if (!resp.ok || !json.ok) {
          flash(json.error || "Uložení selhalo.", true);
          btn.disabled = false;
          return;
        }
        DATA[type] = json.items;
        renderView();
        flash(json.message || "Uloženo.");
      } catch (_err) {
        flash("Chyba spojení.", true);
        btn.disabled = false;
      }
    }

    function renderEdit() {
      editing = true;
      if (toggle) toggle.classList.add("on");
      const tb = el("tbody");
      DATA[type].forEach((item) => tb.append(editRow(item)));
      if (cfg.sortable) {
        tb.addEventListener("dragover", (e) => {
          if (!dragRow) return;
          e.preventDefault();
          const tr = e.target.closest("tr");
          if (!tr || tr === dragRow || tr.parentNode !== tb) return;
          const rect = tr.getBoundingClientRect();
          const after = e.clientY - rect.top > rect.height / 2;
          tb.insertBefore(dragRow, after ? tr.nextSibling : tr);
        });
      }

      const add = el("button", { type: "button", class: "cp-add" }, "+ Přidat");
      add.addEventListener("click", () => {
        const row = editRow({});
        tb.append(row);
        const first = row.querySelector("input, select");
        if (first) first.focus();
      });
      const saveBtn = el("button", { type: "button", class: "cp-primary" }, "Uložit");
      saveBtn.addEventListener("click", () => save(saveBtn));
      const cancel = el("button", { type: "button", class: "cp-cancel" }, "Zrušit");
      cancel.addEventListener("click", renderView);

      // header: drag-handle column (if sortable) + data columns + delete column
      const headings = [...(cfg.sortable ? [""] : []), ...cfg.cols.map((c) => c.label), ""];
      body.replaceChildren(buildTable(headings, tb), el("div", { class: "cp-edit-actions" }, add, saveBtn, cancel));
    }

    if (toggle) toggle.addEventListener("click", () => (editing ? renderView() : renderEdit()));
    renderView();
  });

  // --- tabs ------------------------------------------------------------------
  // The three panes are server-rendered and stay mounted; clicking a tab only toggles
  // which pane is visible, so an in-progress edit in another pane is never destroyed.
  const tabbar = document.querySelector("[data-tax-tabbar]");
  if (tabbar) {
    const panes = {};
    document.querySelectorAll("[data-tax-pane]").forEach((p) => (panes[p.dataset.taxPane] = p));
    const buttons = [...tabbar.querySelectorAll("[data-tax-tab]")];
    const show = (key) => {
      buttons.forEach((b) => b.classList.toggle("on", b.dataset.taxTab === key));
      for (const k in panes) panes[k].hidden = k !== key;
    };
    buttons.forEach((b) => b.addEventListener("click", () => show(b.dataset.taxTab)));
    show((buttons.find((b) => b.classList.contains("on")) || buttons[0]).dataset.taxTab);
  }
})();
