// Camp Planner — Google Calendar tab (camp settings page).
//
// Renders the connect / connected views from the JSON the server embeds in
// #cp-google-data (no fetch on load), and drives connect / disconnect / sync / the
// reviewed inbound import through the /api endpoints via cpDom.api. The inbound review is
// shown inline beneath the buttons (not a modal), with a loading spinner while it loads.
// Loaded only when the feature is configured and the user may edit. No build step.
"use strict";

(function () {
  const root = document.querySelector("[data-google-root]");
  const dataEl = document.getElementById("cp-google-data");
  if (!root || !dataEl) return;

  const { el, api, toast, flash, swatch, keyList, openModal, plural } = window.cpDom;
  const DATA = JSON.parse(dataEl.textContent);
  const URLS = DATA.urls;
  const body = root.querySelector("[data-google-body]");
  const flashArea = root.querySelector("[data-google-flash]");
  const UNKNOWN_WARNING =
    "Neexistující orgové budou z události odstraněni při jakékoliv změně v Camp Planneru. " +
    "Zvažte, zda chcete pokračovat.";
  const FOREIGN_WARNING =
    "Některé události v Google kalendáři už byly sdílené s jinou akcí Camp Planneru (obsahují " +
    "ID slotů, která nepatří této akci). Lze je importovat, ale po importu budou čísla slotů " +
    "v Google kalendáři přepsána (nebudou již spojena s původní akcí).";
  const hm = (iso) => iso.slice(11, 16);            // "…T14:00:00" → "14:00"
  const day = (iso) => iso.slice(0, 10);             // "2026-07-04"
  const range = (s, e) => `${day(s)} ${hm(s)}–${hm(e)}`;
  let status = DATA.status;
  const POLL_MS = 5000;            // re-check queued-op counts while the settings page is open
  let statusInfoEl = null;         // the pending/failed block inside the connected view (re-rendered in place)
  let syncBtn = null;              // "Synchronizovat nyní" — disabled when nothing is queued

  // n is always ≥ 1 here; cpDom.plural handles the 1 / 2–4 / 5+ agreement.
  function pendingMessage(n) {
    const noun = plural(n, "změna", "změny", "změn");
    const waits = plural(n, "čeká", "čekají", "čeká");
    const sends = n === 1 ? "Odešle se" : "Odešlou se";
    return `${n} ${noun} ${waits} na odeslání do Google. ${sends} automaticky na pozadí, `
      + "nebo je odešlete hned tlačítkem „Synchronizovat nyní“.";
  }

  // Reflect the queued-op count on the "Google Calendar" tab button (lives outside our root).
  function updateBadge() {
    const badge = document.querySelector("[data-google-badge]");
    if (!badge) return;
    const n = status.pending_ops || 0;
    badge.textContent = n || "";
    badge.hidden = !n;
    badge.classList.toggle("cp-google-badge-fail", !!status.failed_ops);
  }

  // The pending + failed notices, as standalone nodes so they can be refreshed by polling
  // without rebuilding (and wiping) the rest of the connected view or an open review table.
  function statusInfoNodes() {
    const nodes = [];
    if (status.pending_ops) {
      nodes.push(el("p", { class: "cp-google-pending" }, pendingMessage(status.pending_ops)));
    }
    if (status.failed_ops) {
      nodes.push(el("div", { class: "cp-google-error" },
        el("strong", null, `${status.failed_ops} změn se nepodařilo odeslat do Google.`),
        el("div", null, "Zkontrolujte, že je kalendář sdílený se service accountem "
          + "s právem „Provádět změny v událostech“."),
        status.last_error ? el("div", { class: "cp-google-error-detail" }, "Chyba: " + status.last_error) : null));
    }
    return nodes;
  }

  function refreshStatusInfo() {
    if (statusInfoEl) statusInfoEl.replaceChildren(...statusInfoNodes());
  }

  // Nothing queued → nothing to push, so the manual-sync button is disabled.
  function applySyncState() {
    if (!syncBtn) return;
    const has = !!status.pending_ops;
    syncBtn.disabled = !has;
    syncBtn.title = has ? "" : "Žádné změny k odeslání.";
  }

  // Run an api call with the trigger button disabled; on success swap in the fresh status
  // (every connect/disconnect/sync endpoint returns {google: …}) and re-render.
  async function call(btn, method, url, payload) {
    const label = btn.textContent;
    btn.disabled = true;
    btn.textContent = "…";
    try {
      const json = await api(method, url, payload);
      status = json.google;
      render();
      return json;
    } catch (err) {
      flash(flashArea, err.message, true);
      btn.disabled = false;
      btn.textContent = label;
      return null;
    }
  }

  function disconnectedView() {
    const input = el("input", {
      type: "text", class: "cp-google-input",
      placeholder: "ID kalendáře, např. abc123@group.calendar.google.com",
    });
    const connect = el("button", { type: "button", class: "cp-primary" }, "Připojit");
    connect.addEventListener("click", () => {
      const calendar_id = input.value.trim();
      if (!calendar_id) { flash(flashArea, "Zadejte ID kalendáře.", true); return; }
      call(connect, "PUT", URLS.base, { calendar_id });
    });
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") connect.click(); });

    return el("div", { class: "cp-google" },
      el("div", { class: "cp-google-info" },
        el("p", null, "Obousměrná synchronizace časového plánu s Google Kalendářem."),
        el("ol", { class: "cp-google-steps" },
          el("li", null,
            "Vytvořte (sekundární) kalendář a nasdílejte ho s účtem ",
            el("code", null, status.service_account_email || "—"),
            " s právem ", el("strong", null, "Provádět změny v událostech"), "."),
          el("li", null,
            "Zkopírujte ", el("strong", null, "ID kalendáře"),
            " (Nastavení kalendáře → „Integrace kalendáře“) a vložte ho sem:")),
        el("div", { class: "cp-google-row" }, input, connect)));
  }

  function connectedView() {
    const sync = el("button", { type: "button", class: "cp-mini" }, "Synchronizovat nyní");
    sync.addEventListener("click", async () => {
      const json = await call(sync, "POST", URLS.sync);
      if (json) {
        const r = json.result;
        toast(`Odesláno: ${r.pushed}` + (r.failed ? `, chyb: ${r.failed}` : ""), r.failed > 0);
      }
    });
    syncBtn = sync;

    const resync = el("button", { type: "button", class: "cp-mini" }, "Znovu synchronizovat vše");
    resync.title = "Zařadí všechny sloty k odeslání do Google — oprava, když se kalendář rozejde.";
    resync.addEventListener("click", async () => {
      if (!window.confirm("Zařadit všechny sloty k opětovnému odeslání do Google?")) return;
      const json = await call(resync, "POST", URLS.resync);
      if (json) {
        const n = json.result.queued;
        toast(`Zařazeno k synchronizaci: ${n} ${plural(n, "slot", "sloty", "slotů")}.`);
      }
    });

    const review = el("div", { class: "cp-google-review" });
    const pull = el("button", { type: "button", class: "cp-mini" }, "Načíst změny z Google");
    pull.addEventListener("click", () => loadReview(review, pull));

    const disconnect = el("button", { type: "button", class: "cp-mini cp-danger" }, "Odpojit");
    disconnect.addEventListener("click", () => {
      if (!window.confirm("Odpojit kalendář? Události už v Google zůstanou, jen se přestanou synchronizovat.")) return;
      call(disconnect, "DELETE", URLS.base);
    });

    statusInfoEl = el("div", { class: "cp-google-status" }, ...statusInfoNodes());

    return el("div", { class: "cp-google" },
      el("div", { class: "cp-google-info" },
        el("p", null, "Připojeno ke kalendáři: ", el("code", null, status.calendar_id)),
        statusInfoEl,
        el("div", { class: "cp-google-row" }, sync, resync, pull, disconnect)),
      review);
  }

  // The "Kdy" (when) cell text for a change.
  function whenText(change) {
    if (change.kind === "time_change") {
      return `${range(change.old_start, change.old_end)} → ${range(change.new_start, change.new_end)}`;
    }
    if (change.kind === "new_event") return range(change.new_start, change.new_end);
    if (change.old_start) return range(change.old_start, change.old_end);  // deleted / attendants
    return "—";                                                            // garant / category (activity-level)
  }

  // One reviewable inbound change → a block of rows. ROW1: checkbox (rowspan over the whole
  // block) · the change name (colspan 2) · the action (rowspan; "Importovat jako" for new
  // events). Following rows: one detail per row (label + value). Returns
  // { nodes, cb, setEnabled, decide } — setEnabled greys the block and disables its controls;
  // decide() yields the decision object or null when unchecked.
  function changeRow(change, activities, categories) {
    const cb = el("input", { type: "checkbox", checked: true });
    const controls = [];                  // form controls disabled when the row is unchecked
    let attachBtn = null, attachAlwaysOff = false;

    const list = (a) => a.join(", ") || "—";
    const fromTo = (oldv, newv) => `${list(oldv)} → ${list(newv)}`;  // old → new, like time changes

    const details = [];
    const when = whenText(change);
    if (when !== "—") details.push(["Datum", when]);
    if (change.kind === "attendants_change") {
      details.push(["Účastníci", fromTo(change.old_initials, change.new_initials)]);
    } else if (change.kind === "garant_change") {
      details.push(["Garanti", fromTo(change.old_garants, change.new_garants)]);
      if (change.old_helpers.length || change.new_helpers.length) {
        details.push(["Pomocníci", fromTo(change.old_helpers, change.new_helpers)]);
      }
    } else if (change.kind === "category_change") {
      details.push(["Kategorie", `${change.old_label} → ${change.new_label}`]);
    } else if (change.kind === "new_event") {
      if (change.garant_initials.length) details.push(["Garanti", change.garant_initials.join(", ")]);
      if (change.helper_initials.length) details.push(["Pomocníci", change.helper_initials.join(", ")]);
      if (change.attendant_initials.length) details.push(["Účastníci", change.attendant_initials.join(", ")]);
      if (change.foreign_slot) details.push(["⚠", "Nesedící slot ID", "cp-google-warn"]);
    }
    if (change.unknown && change.unknown.length) {
      details.push(["Pozor", `Neznámí orgové: ${change.unknown.join(", ")}. ${UNKNOWN_WARNING}`, "cp-google-warn"]);
    }

    // action cell (new events choose how to import; others are just apply/skip via checkbox)
    let action = "";
    let decide;
    if (change.kind === "new_event") {
      let mode = "new";  // "new" → create activity (+category) | "attach" → existing activity

      const newBtn = el("button", { type: "button", class: "cp-google-seg on" }, "Nová aktivita");
      attachBtn = el("button", { type: "button", class: "cp-google-seg" }, "Přidat k existující");
      attachAlwaysOff = !activities.length;  // nothing to attach to yet
      attachBtn.disabled = attachAlwaysOff;
      controls.push(newBtn, attachBtn);
      const toggle = el("div", { class: "cp-google-toggle" }, newBtn, attachBtn);

      // category — compact button showing the current pick; click opens a small chooser modal
      let catId = change.category_id != null ? change.category_id : "";  // "" = bez kategorie
      const catBtn = el("button", { type: "button", class: "cp-google-pick" });
      const renderCatBtn = () => {
        const c = categories.find((x) => x.id === catId);
        catBtn.replaceChildren(swatch(c ? c.color : null), el("span", null, c ? c.label : "bez kategorie"));
      };
      catBtn.addEventListener("click", () => {
        let close;
        const opts = [["", null, "bez kategorie"], ...categories.map((c) => [c.id, c.color, c.label])];
        const chips = opts.map(([id, color, label]) => {
          const chip = el("button", { type: "button", class: "cp-cat-chip" + (id === catId ? " on" : "") },
            swatch(color), " " + label);
          chip.addEventListener("click", () => { catId = id; renderCatBtn(); close(); });
          return chip;
        });
        close = openModal(el("div", { class: "cp-modal cp-google-pick-modal" },
          el("div", { class: "cp-modal-head" }, "Vyberte kategorii"),
          el("div", { class: "cp-cat-chips cp-google-pick-body" }, ...chips)));
      });
      renderCatBtn();
      controls.push(catBtn);
      const catField = el("div", { class: "cp-google-field" },
        el("span", { class: "cp-google-flabel" }, "Kategorie:"), catBtn);

      // existing activity — compact button; click opens a modal with the fuzzy search picker
      let chosen = activities[0] || null;
      const actBtn = el("button", { type: "button", class: "cp-google-pick" });
      const renderActBtn = () => actBtn.replaceChildren(el("span", null, chosen ? chosen.title : "— vyberte —"));
      actBtn.addEventListener("click", () => {
        let close;
        const search = el("input", { type: "text", class: "cp-modal-search", placeholder: "Hledat aktivitu…" });
        const listEl = el("div", { class: "cp-modal-list" });
        const setRows = keyList(search);
        const renderList = (q) => {
          const query = q.trim();
          const matches = query && window.cpFuzzy
            ? window.cpFuzzy.filter(query, activities, (a) => a.title)
            : activities;
          const entries = matches.map((a) => ({
            el: el("button", { type: "button", class: "cp-modal-item" }, a.title),
            pick: () => { chosen = a; renderActBtn(); close(); },
          }));
          listEl.replaceChildren(...(entries.length ? entries.map((e) => e.el)
            : [el("div", { class: "cp-muted" }, "Nic nenalezeno.")]));
          setRows(entries);
        };
        search.addEventListener("input", () => renderList(search.value));
        close = openModal(el("div", { class: "cp-modal cp-google-pick-modal" },
          el("div", { class: "cp-modal-head" }, "Vyberte aktivitu"),
          el("div", { class: "cp-google-pick-search" }, search),
          listEl));
        renderList("");
        search.focus();
      });
      renderActBtn();
      controls.push(actBtn);
      const actField = el("div", { class: "cp-google-field" },
        el("span", { class: "cp-google-flabel" }, "Aktivita:"), actBtn);

      const syncMode = () => {
        newBtn.classList.toggle("on", mode === "new");
        attachBtn.classList.toggle("on", mode === "attach");
        catField.hidden = mode !== "new";
        actField.hidden = mode !== "attach";
      };
      newBtn.addEventListener("click", () => { mode = "new"; syncMode(); });
      attachBtn.addEventListener("click", () => { mode = "attach"; syncMode(); });
      syncMode();

      action = el("div", { class: "cp-google-import" }, toggle, catField, actField);
      decide = () => {
        if (!cb.checked) return null;
        if (mode === "new") return { key: change.key, action: "new", category_id: catId ? Number(catId) : null };
        return { key: change.key, action: "attach", target_activity_id: chosen ? chosen.id : null };
      };
    } else {
      decide = () => (cb.checked ? { key: change.key, action: "apply" } : null);
    }

    const kindClass = "cp-kind-" + change.kind;
    const span = 1 + details.length;
    const nodes = [
      el("tr", { class: kindClass + " cp-google-row1" },
        el("td", { class: "cp-google-check", rowspan: span }, cb),
        el("td", { class: "cp-google-label", colspan: 2 }, change.label),
        el("td", { class: "cp-google-action", rowspan: span }, action)),
    ];
    for (const [name, value, cls] of details) {
      nodes.push(el("tr", { class: kindClass },
        el("td", { class: "cp-google-dname" }, name),
        el("td", { class: "cp-google-dvalue" + (cls ? " " + cls : "") }, value)));
    }

    const setEnabled = (on) => {
      nodes.forEach((r) => r.classList.toggle("cp-google-off", !on));
      controls.forEach((c) => { c.disabled = !on; });
      if (attachBtn && attachAlwaysOff) attachBtn.disabled = true;  // stays off when nothing to attach to
    };
    cb.addEventListener("change", () => setEnabled(cb.checked));

    return { nodes, cb, setEnabled, decide };
  }

  async function loadReview(area, trigger) {
    trigger.disabled = true;
    area.replaceChildren(el("div", { class: "cp-google-loading" },
      el("span", { class: "cp-spinner" }), "Načítám změny z Google…"));
    let preview;
    try {
      preview = await api("GET", URLS.pull);
    } catch (err) {
      area.replaceChildren();
      flash(flashArea, err.message, true);
      trigger.disabled = false;
      return;
    }
    trigger.disabled = false;

    if (!preview.changes.length) {
      area.replaceChildren(el("p", { class: "cp-muted" }, "Žádné nové změny v Google kalendáři."));
      return;
    }

    const rows = preview.changes.map((c) => changeRow(c, preview.activities, preview.categories));
    const apply = el("button", { type: "button", class: "cp-primary" }, "Použít vybrané");
    const cancel = el("button", { type: "button", class: "cp-cancel" }, "Zavřít");
    cancel.addEventListener("click", () => area.replaceChildren());

    apply.addEventListener("click", async () => {
      const decisions = rows.map((r) => r.decide()).filter(Boolean);
      if (!decisions.length) { area.replaceChildren(); return; }
      apply.disabled = true;
      try {
        const json = await api("POST", URLS.pull, { rev: preview.rev, decisions });
        const a = json.applied;
        toast(`Importováno: ${a.imported_slots}, upraveno: ${a.updated}, smazáno: ${a.deleted}.`);
        const fresh = await api("GET", URLS.base);  // refresh queued-op count
        status = fresh.google;
        render();  // rebuilds the connected view (and clears the review area)
      } catch (err) {
        apply.disabled = false;
        flash(flashArea, err.message, true);
      }
    });

    const selectAll = el("input", { type: "checkbox", checked: true });
    selectAll.addEventListener("change", () => rows.forEach((r) => {
      r.cb.checked = selectAll.checked;
      r.setEnabled(selectAll.checked);
    }));
    const table = el("table", { class: "cp-google-table" },
      el("thead", null, el("tr", null,
        el("th", { class: "cp-google-check" }, selectAll),
        el("th", { colspan: 2 }, "Změna"),
        el("th", null, "Akce"))),
      el("tbody", null, ...rows.flatMap((r) => r.nodes)));

    // Warn (inline, atop the list) when any change is an event that carried another camp's slot
    // id — importing it rewrites that marker in Google. Shown here rather than in a confirm.
    const children = [el("h3", { class: "cp-google-review-title" }, "Změny z Google kalendáře")];
    if (preview.changes.some((c) => c.foreign_slot)) {
      children.push(el("div", { class: "cp-google-error cp-google-foreign" }, FOREIGN_WARNING));
    }
    children.push(table, el("div", { class: "cp-google-review-actions" }, apply, cancel));
    area.replaceChildren(...children);
  }

  function render() {
    statusInfoEl = null;  // dropped if we render the disconnected view; connectedView resets them
    syncBtn = null;
    body.replaceChildren(status.connected ? connectedView() : disconnectedView());
    updateBadge();
    applySyncState();
  }

  // Poll the status endpoint while the settings page is open so the queued-op count (tab badge
  // + pending notice) tracks background drains without a reload. Only the status notice is
  // refreshed in place — never the buttons or an open review table — and a flipped connection
  // state triggers a full re-render. Transient errors are ignored; the next tick retries.
  async function poll() {
    if (!status.connected) return;  // disconnected → nothing can be queued; skip the request (timer stays alive)
    let fresh;
    try {
      fresh = await api("GET", URLS.base);
    } catch {
      return;
    }
    const wasConnected = status.connected;
    status = fresh.google;
    if (status.connected !== wasConnected) {
      render();
    } else {
      updateBadge();
      refreshStatusInfo();
      applySyncState();
    }
  }

  // Only poll while the tab is in the foreground; pause the timer entirely when it's hidden
  // (no background wake-ups), and refresh once immediately on return so the count isn't stale.
  let pollTimer = null;
  const startPoll = () => { if (pollTimer === null) pollTimer = setInterval(poll, POLL_MS); };
  const stopPoll = () => { if (pollTimer !== null) { clearInterval(pollTimer); pollTimer = null; } };
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { stopPoll(); } else { poll(); startPoll(); }
  });

  render();
  if (!document.hidden) startPoll();
})();
