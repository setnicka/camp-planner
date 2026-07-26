// Camp Planner — API tokens tab (camp settings page).
//
// Renders the token list + create form from the JSON the server embeds in
// #cp-tokens-data (no fetch on load) and drives create / revoke through the /api
// endpoints via cpDom.api. The secret is shown once, in a modal, right after creation.
// Loaded only when the user may edit; the api re-checks server-side.
"use strict";

(function () {
  const root = document.querySelector("[data-tokens-root]");
  const dataEl = document.getElementById("cp-tokens-data");
  if (!root || !dataEl) return;

  const { el, api, toast, flash, openModal, withId } = window.cpDom;
  const DATA = JSON.parse(dataEl.textContent);
  const URLS = DATA.urls;
  const ROLE_LABEL = Object.fromEntries(DATA.roles);
  const body = root.querySelector("[data-tokens-body]");
  const flashArea = root.querySelector("[data-tokens-flash]");
  let tokens = DATA.tokens.slice();

  // created_at / last_used_at are naive UTC (like the audit feed) — mark them UTC so the
  // browser renders them in local time; null last_used_at → em dash.
  const fmtTs = (iso) => (iso
    ? new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z").toLocaleString("cs-CZ",
        { day: "numeric", month: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit" })
    : "—");

  function render() {
    body.replaceChildren(createForm(), listView());
  }

  function createForm() {
    const name = el("input", { type: "text", maxlength: "255", placeholder: "např. import-script" });
    const role = el("select");
    DATA.roles.forEach(([value, label]) => role.add(new Option(label, value)));
    const btn = el("button", { type: "button" }, "Vytvořit token");
    const submit = () => createToken(name, role, btn);
    btn.addEventListener("click", submit);
    name.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });
    return el("div", { class: "cp-token-new" },
      el("div", { class: "cp-form cp-form-inline" },
        el("label", null, "Název", name),
        el("label", null, "Role", role),
        btn),
      el("div", { class: "cp-field-hint" },
        "Token přistupuje k této akci přes REST API (hlavička ",
        el("code", null, "Authorization: Bearer …"),
        "). Tajný klíč se zobrazí jen jednou po vytvoření."));
  }

  async function createToken(name, role, btn) {
    const nm = name.value.trim();
    if (!nm) { name.focus(); return; }
    btn.disabled = true;
    try {
      const json = await api("POST", URLS.list, { name: nm, role: role.value });
      tokens.push(json.token);
      render();
      showSecret(json.token, json.secret);
    } catch (e) {
      toast(e.message, true);
    } finally {
      btn.disabled = false;
    }
  }

  function listView() {
    if (!tokens.length) return el("p", { class: "cp-muted" }, "Zatím žádné tokeny.");
    const rows = tokens.slice()
      .sort((a, b) => a.name.localeCompare(b.name, "cs"))
      .map(tokenRow);
    return el("table", { class: "cp-table" },
      el("thead", null, el("tr", null,
        el("th", null, "Název"), el("th", null, "Role"), el("th", null, "Vytvořil"),
        el("th", null, "Vytvořeno"), el("th", null, "Naposledy použit"), el("th", null, ""))),
      el("tbody", null, ...rows));
  }

  function tokenRow(t) {
    const del = el("button", { type: "button", class: "cp-danger" }, "Zrušit");
    del.addEventListener("click", () => revoke(t, del));
    return el("tr", null,
      el("td", null, t.name),
      el("td", null, ROLE_LABEL[t.role] || t.role),
      el("td", null, t.created_by),
      el("td", null, fmtTs(t.created_at)),
      el("td", null, fmtTs(t.last_used_at)),
      el("td", { class: "cp-actions" }, del));
  }

  async function revoke(t, btn) {
    if (!window.confirm(`Zrušit token „${t.name}“? Skripty, které ho používají, ztratí přístup.`)) return;
    btn.disabled = true;
    try {
      await api("DELETE", withId(URLS.item, t.id));
      tokens = tokens.filter((x) => x.id !== t.id);
      render();
      flash(flashArea, `Token „${t.name}“ zrušen.`);
    } catch (e) {
      toast(e.message, true);
      btn.disabled = false;
    }
  }

  // One-time reveal of the secret: a read-only field + copy button. Once the modal closes
  // the secret is gone (only its SHA-256 is stored server-side).
  function showSecret(token, secret) {
    const field = el("input", { type: "text", class: "cp-token-secret", value: secret, readonly: true });
    const copy = el("button", { type: "button", class: "cp-mini" }, "Kopírovat");
    copy.addEventListener("click", () => {
      navigator.clipboard?.writeText(secret).then(
        () => { copy.textContent = "Zkopírováno"; },
        () => { field.select(); });
    });
    const done = el("button", { type: "button", class: "cp-primary" }, "Hotovo");
    const dialog = el("div", { class: "cp-modal cp-modal-wide" },
      el("div", { class: "cp-modal-head" }, `Token „${token.name}“ vytvořen`),
      el("div", { class: "cp-pane" },
        el("p", null, "Zkopírujte si tajný klíč hned — ", el("b", null, "už se znovu nezobrazí"), "."),
        el("div", { class: "cp-token-secretrow" }, field, copy),
        el("div", { class: "cp-field-hint" }, "Použití: hlavička ",
          el("code", null, "Authorization: Bearer <klíč>"), ".")),
      el("div", { class: "cp-modal-foot" }, done));
    const close = openModal(dialog);
    done.addEventListener("click", () => close());
    field.focus();
    field.select();
  }

  render();
})();
