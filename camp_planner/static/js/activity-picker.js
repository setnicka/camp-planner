// Camp Planner — activity-picker modal (the timeline editor's "add slot" dialog).
// Two tabs — pick an existing activity (fuzzy, last-used first) or create a new one —
// plus the slot-role choice. Calls onConfirm(activity, role), or onCancel() when
// dismissed without a pick. Exposed as window.cpActivityPicker; load after dom.js.
"use strict";

window.cpActivityPicker = (function () {
  const { el, api, swatch, openModal, chipGroup, keyList, toast } = window.cpDom;
  let cache = null;   // [{id, title, category_id}, …], lazy-loaded once per page

  async function fetchActivities(url) {
    if (cache) return cache;
    const json = await api("GET", url);
    cache = (json.activities || []).map((a) => ({ id: a.id, title: a.title, category_id: a.category_id }));
    return cache;
  }

  return function ({ activitiesUrl, createUrl, campSlug, categories, roleLabels, onConfirm, onCancel }) {
    const catById = Object.fromEntries(categories.map((c) => [c.id, c]));
    const RECENT_KEY = "cp-recent-activities:" + campSlug;
    const recentIds = () => {
      try { return JSON.parse(localStorage.getItem(RECENT_KEY) || "[]"); } catch (_e) { return []; }
    };
    const rememberRecent = (id) => {
      const next = [id, ...recentIds().filter((x) => x !== id)].slice(0, 3);
      try { localStorage.setItem(RECENT_KEY, JSON.stringify(next)); } catch (_e) { /* ignore */ }
    };

    // stays undefined on any dismissal (Escape / backdrop / Zrušit) → onCancel
    let picked;
    // slot type (role) — applies to both tabs (existing pick or freshly created); main default.
    const roles = chipGroup(Object.entries(roleLabels), { selected: "main" });
    const finish = (activity) => {
      if (activity) { picked = activity; rememberRecent(activity.id); close(); onConfirm(activity, roles.get()); }
      else close();
    };
    const roleRow = el("div", { class: "cp-modal-role" }, el("span", { class: "cp-modal-role-label" }, "Typ slotu:"), roles.node);

    const search = el("input", { type: "text", class: "cp-modal-search", placeholder: "Hledat aktivitu…" });
    const list = el("div", { class: "cp-modal-list" });
    const nameInput = el("input", { type: "text", class: "cp-modal-name", placeholder: "Název nové aktivity" });
    // category chips, not <option> (native option colors are ignored by most browsers)
    const noCats = categories.length === 0;
    const cats = chipGroup(
      categories.map((c) => [c.id, swatch(c.color), c.label]),
      { selected: noCats ? null : categories[0].id });
    const createBtn = el("button", { type: "button", class: "cp-primary", disabled: noCats }, "Vytvořit a přidat");

    // keyboard-navigable existing-activity list (cpDom.keyList): arrows move, Enter picks
    const setRows = keyList(search);
    function renderList(query) {
      const all = cache || [];
      const q = query.trim();
      const recentSet = new Set(q ? [] : recentIds());
      let acts;
      if (q) {
        acts = window.cpFuzzy
          ? window.cpFuzzy.filter(q, all, (a) => a.title)               // diacritics-folded fuzzy
          : all.filter((a) => a.title.toLowerCase().includes(q.toLowerCase()));
      } else {
        const byId = Object.fromEntries(all.map((a) => [a.id, a]));     // only the recents path needs it
        const recent = [...recentSet].map((id) => byId[id]).filter(Boolean);
        acts = [...recent, ...all.filter((a) => !recentSet.has(a.id))];
      }
      if (!acts.length) { list.replaceChildren(el("div", { class: "cp-muted" }, "Nic nenalezeno.")); setRows([]); return; }
      const entries = acts.map((a) => {
        const cat = catById[a.category_id];
        return {
          el: el("button", { type: "button", class: "cp-modal-item" },
            swatch(cat?.color),
            el("span", null, a.title),
            recentSet.has(a.id) ? el("span", { class: "cp-modal-recent" }, "naposledy") : null),
          pick: () => finish(a),
        };
      });
      list.replaceChildren(...entries.map((e) => e.el));
      setRows(entries);
    }
    search.addEventListener("input", () => renderList(search.value));

    async function createActivity() {
      const title = nameInput.value.trim();
      if (!title) { nameInput.focus(); return; }
      const categoryId = cats.get();
      if (categoryId == null) { toast("Vyberte kategorii.", true); return; }
      createBtn.disabled = true;
      try {
        const json = await api("POST", createUrl, { title, category_id: categoryId });
        const a = { id: json.activity.id, title: json.activity.title, category_id: json.activity.category_id };
        if (cache) cache.unshift(a);
        finish(a);
        toast("Aktivita vytvořena");
      } catch (e) { toast(e.message, true); createBtn.disabled = false; }
    }
    createBtn.addEventListener("click", createActivity);
    nameInput.addEventListener("keydown", (e) => { if (e.key === "Enter") createActivity(); });

    // tabs
    const tabExisting = el("button", { type: "button", class: "cp-tab on" }, "Existující");
    const tabNew = el("button", { type: "button", class: "cp-tab" }, "Nová");
    const paneExisting = el("div", { class: "cp-pane" }, search, list);
    const paneNew = el("div", { class: "cp-pane", hidden: true }, nameInput, cats.node, createBtn,
      noCats ? el("div", { class: "cp-modal-hint" }, "Nejprve vytvořte kategorii v nastavení akce.") : null);
    const selectTab = (newTab) => {
      tabExisting.classList.toggle("on", !newTab); tabNew.classList.toggle("on", newTab);
      paneExisting.hidden = newTab; paneNew.hidden = !newTab;
      (newTab ? nameInput : search).focus();
    };
    tabExisting.addEventListener("click", () => selectTab(false));
    tabNew.addEventListener("click", () => selectTab(true));

    const dialog = el("div", { class: "cp-modal" },
      el("div", { class: "cp-modal-tabs" }, tabExisting, tabNew),
      roleRow,
      paneExisting, paneNew,
      el("div", { class: "cp-modal-foot" },
        el("button", { type: "button", class: "cp-cancel" }, "Zrušit")));
    dialog.querySelector(".cp-cancel").addEventListener("click", () => finish(null));
    const close = openModal(dialog, () => { if (picked === undefined) onCancel(); });

    fetchActivities(activitiesUrl).then(() => renderList("")).catch((e) => { toast(e.message, true); renderList(""); });
    search.focus();
  };
})();
