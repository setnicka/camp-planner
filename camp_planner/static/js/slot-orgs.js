// Camp Planner — shared slot-edit dialog (attendees, optionally the name override).
//
// One modal for editing a time block, used by both the timeline editor and the activity
// detail page so the two stay identical. Multi-select chip group picks who staffs the
// block; pass `withName: true` to also show a display-name field. PATCHes the given url
// with the changed fields, then hands the saved state to onSaved(orgs, ids, overrideName).
// Exposed as window.cpSlotOrgsEdit; load after dom.js.
"use strict";

window.cpSlotOrgsEdit = function ({ orgs, selected, url, withName, name, namePlaceholder, onSaved }) {
  const { el, api, chipGroup, formModal, toast } = window.cpDom;
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
  formModal({
    title: withName ? "Upravit slot" : "Orgové bloku",
    pane: el("div", { class: "cp-pane" }, nameField, group.node),
    onSubmit: async (close) => {
      const ids = group.get();
      const body = { org_ids: ids };
      if (withName) body.override_name = nameInput.value;
      const json = await api("PATCH", url, body);
      close();
      onSaved(json.orgs, ids, json.override_name);
      toast("Uloženo");
    },
  });
};
