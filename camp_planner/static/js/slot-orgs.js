// Camp Planner — shared slot-edit dialog (attendees, optionally the name override).
//
// One modal for editing a time block, used by both the timeline editor and the activity
// detail page so the two stay identical. Multi-select chip group picks who staffs the
// block; pass `withName: true` to also show a display-name field (activity detail does,
// the timeline editor has its own name dialog and omits it). PATCHes the given url with
// the changed fields, then hands the saved state to onSaved(orgs, ids, overrideName).
// Exposed as window.cpSlotOrgsEdit; load after dom.js.
"use strict";

window.cpSlotOrgsEdit = function ({ orgs, selected, url, withName, name, namePlaceholder, onSaved }) {
  const { el, api, chipGroup, openModal, toast } = window.cpDom;
  const group = chipGroup(orgs.map((o) => [o.id, el("b", null, o.initials), " " + o.name]),
    { multi: true, selected: selected || [] });
  if (!orgs.length) group.node.append(el("div", { class: "cp-muted" }, "Žádní orgové — přidejte je v nastavení akce."));
  const nameInput = withName
    ? el("input", { type: "text", class: "cp-modal-name", maxlength: 255,
        placeholder: namePlaceholder || "", value: name || "" })
    : null;
  const nameField = nameInput
    ? el("div", { class: "cp-field" },
        el("label", { class: "cp-field-label" }, "Speciální název slotu (prázdný název defaultuje na název aktivity)"), nameInput)
    : null;
  const cancel = el("button", { type: "button", class: "cp-cancel" }, "Zrušit");
  const ok = el("button", { type: "button", class: "cp-primary" }, "Uložit");
  const dialog = el("div", { class: "cp-modal cp-modal-wide" },
    el("div", { class: "cp-modal-head" }, withName ? "Upravit slot" : "Orgové bloku"),
    el("div", { class: "cp-pane" }, nameField, group.node),
    el("div", { class: "cp-modal-foot" }, cancel, ok));
  const close = openModal(dialog);
  cancel.addEventListener("click", close);
  ok.addEventListener("click", async () => {
    ok.disabled = true;
    const ids = group.get();
    const body = { org_ids: ids };
    if (withName) body.override_name = nameInput.value;
    try {
      const json = await api("PATCH", url, body);
      close();
      onSaved(json.orgs, ids, json.override_name);
      toast("Uloženo");
    } catch (e) { ok.disabled = false; toast(e.message, true); }
  });
};
