// Camp Planner: iCal export modal (timeline page).
//
// Builds a calendar-subscription URL for the token-guarded /ical/<slug> feed. Filters
// reuse the timeline's "type:value" grammar (repeated filter= params). The token is
// remembered per camp in localStorage only; the server never stores calendar URLs.
"use strict";

(function () {
  const btn = document.getElementById("cp-ical-btn");
  const dataEl = document.getElementById("cp-ical-data");
  const tlEl = document.getElementById("cp-timeline-data");
  if (!btn || !dataEl || !tlEl || !window.cpDom || !window.cpTimelineKit) return;

  const { el, api, toast, openModal, submit, swatch, chipGroup } = window.cpDom;
  const { NO_CATEGORY, orgCycle } = window.cpTimelineKit;
  const DATA = JSON.parse(dataEl.textContent);
  let TL = null;         // the (large) timeline payload, parsed on first open only
  let LS_KEY = "";

  const lsGet = () => { try { return localStorage.getItem(LS_KEY) || ""; } catch (_e) { return ""; } };
  const lsSet = (v) => { try { v ? localStorage.setItem(LS_KEY, v) : localStorage.removeItem(LS_KEY); } catch (_e) { /* private mode */ } };

  // Auto-name for a generated token; seconds keep it unique per camp without a retry.
  function tokenName() {
    const d = new Date();
    const p = (n) => String(n).padStart(2, "0");
    return "ical-" + d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) +
      "-" + p(d.getHours()) + p(d.getMinutes()) + p(d.getSeconds());
  }

  btn.addEventListener("click", () => {
    if (!TL) {
      TL = JSON.parse(tlEl.textContent);
      LS_KEY = "cp-ical-token:" + TL.camp.slug;
    }
    // categories incl. the synthetic "Bez kategorie", mirroring the legend
    const cats = TL.categories.slice();
    if (TL.segments.some((s) => s.cat_key === "_none")) cats.push(NO_CATEGORY);

    // prefill from the active timeline filter (#filter=type:value)
    let pre = null;
    const m = /^#filter=(category|garant|attending):(.+)$/.exec(location.hash);   // activity has no UI here
    if (m) {
      try { pre = { type: m[1], value: decodeURIComponent(m[2]) }; }
      catch (_e) { /* malformed escape in a hand-edited hash: no prefill */ }
    }
    // only prefill values that exist; a stale bookmark must not create an invisible filter
    if (pre && (pre.type === "category"
      ? !cats.some((c) => c.key === pre.value)
      : !TL.orgs.some((o) => String(o.id) === pre.value))) pre = null;

    const token = el("input", {
      type: "text", class: "cp-modal-name cp-ical-token", placeholder: "cp_…",
      value: lsGet(), spellcheck: false,
    });
    const catChips = chipGroup(
      cats.map((c) => [c.key, swatch(c.color), c.label]),
      { multi: true, selected: pre && pre.type === "category" ? [pre.value] : [],
        onChange: () => update() });

    // org chips as above the timeline
    let orgFilter = pre && (pre.type === "garant" || pre.type === "attending")
      ? { type: pre.type, id: Number(pre.value) } : null;
    const orgChips = orgCycle(TL.orgs, (f) => { orgFilter = f; update(); });
    orgChips.set(orgFilter);

    const urlOut = el("textarea", { readOnly: true, class: "cp-act-textarea cp-ical-url", rows: 3, spellcheck: false });
    urlOut.addEventListener("click", () => urlOut.select());
    urlOut.addEventListener("focus", () => urlOut.select());
    const copyBtn = el("button", { type: "button", class: "cp-cancel" }, "Kopírovat");
    const hint = el("div", { class: "cp-field-hint" });

    function update() {
      const t = token.value.trim();
      const q = new URLSearchParams();
      catChips.get().forEach((key) => q.append("filter", "category:" + key));
      if (orgFilter) q.append("filter", orgFilter.type + ":" + orgFilter.id);
      q.append("token", t);
      urlOut.value = location.origin + DATA.feed + "?" + q;
      copyBtn.disabled = !t;
      hint.textContent = t ? "Nevybraný filtr znamená vše. URL vložte do kalendáře jako odběr (Google: „Přidat kalendář z adresy URL“)."
        : "Bez tokenu nebude URL fungovat – vygenerujte si ho, nebo vložte existující.";
    }
    token.addEventListener("input", update);
    // persist on commit, not every keystroke (typing would spam localStorage writes)
    token.addEventListener("change", () => lsSet(token.value.trim()));
    copyBtn.addEventListener("click", () => {
      if (!navigator.clipboard) { urlOut.focus(); return; }   // insecure origin: focus selects all
      navigator.clipboard.writeText(urlOut.value).then(
        () => toast("URL zkopírována."),
        () => { urlOut.focus(); toast("Kopírování selhalo, zkopírujte URL ručně.", true); });
    });

    let genRow = null;
    const tokenHint = el("div", { class: "cp-field-hint" });   // names the generated token
    if (DATA.tokens) {   // the create-token URL is emitted for editors only
      const gen = el("button", { type: "button", class: "cp-cancel" }, "Vygenerovat nový read-only token");
      gen.addEventListener("click", () => {
        if (!window.confirm("Vygeneruje a uloží nový token na serveru, pokračovat?")) return;
        submit(gen, async () => {
          const json = await api("POST", DATA.tokens, { name: tokenName(), role: "viewer" });
          token.value = json.secret;
          lsSet(json.secret);   // programmatic .value set fires no change event
          update();
          tokenHint.textContent = "Vygenerován token „" + json.token.name +
            "“ – zůstává ve správě tokenů v nastavení akce.";
          toast("Token „" + json.token.name + "“ vytvořen a vyplněn.");
        });
      });
      genRow = el("div", null, gen);
    }

    const closeBtn = el("button", { type: "button", class: "cp-cancel" }, "Zavřít");
    const dialog = el("div", { class: "cp-modal cp-modal-wide" },
      el("div", { class: "cp-modal-head" }, "Export do kalendáře (iCal)"),
      el("div", { class: "cp-pane" },
        el("p", { class: "cp-muted" },
          "Vygenerujte si read-only token pro export kalendáře, nebo použijte již existující ",
          "read-only token (nebo ho vložte do URL ručně). Poslední token použitý pro kalendář ",
          "si browser pamatuje lokálně."),
        el("div", { class: "cp-field-label" }, "Token"), token, tokenHint, genRow,
        el("div", { class: "cp-field-label" }, "Kategorie"), catChips.node,
        el("div", { class: "cp-field-label" }, "Org"),
        orgChips.node,
        el("div", { class: "cp-field-label" }, "URL kalendáře"), urlOut,
        el("div", null, copyBtn),
        hint),
      el("div", { class: "cp-modal-foot" }, closeBtn));
    const close = openModal(dialog);
    closeBtn.addEventListener("click", () => close());
    update();
    token.focus();
  });
})();
