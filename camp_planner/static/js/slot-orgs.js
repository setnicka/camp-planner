// Camp Planner — shared "assign orgs to a slot" dialog.
//
// One modal for choosing which orgs staff a time block, used by both the timeline editor and
// the activity detail page so the two stay identical. Multi-select chip group → PUT the given
// url → hands the saved orgs to onSaved(); toasts success / failure. Exposed as
// window.cpSlotOrgsEdit; load after dom.js.
"use strict";

window.cpSlotOrgsEdit = function ({ orgs, selected, url, onSaved }) {
  const { el, api, chipGroup, openModal, toast } = window.cpDom;
  const group = chipGroup(orgs.map((o) => [o.id, el("b", null, o.initials), " " + o.name]),
    { multi: true, selected: selected || [] });
  if (!orgs.length) group.node.append(el("div", { class: "cp-muted" }, "Žádní orgové — přidejte je v nastavení akce."));
  const cancel = el("button", { type: "button", class: "cp-cancel" }, "Zrušit");
  const ok = el("button", { type: "button", class: "cp-primary" }, "Uložit");
  const dialog = el("div", { class: "cp-modal cp-modal-wide" },
    el("div", { class: "cp-modal-head" }, "Orgové bloku"),
    el("div", { class: "cp-pane" }, group.node),
    el("div", { class: "cp-modal-foot" }, cancel, ok));
  const close = openModal(dialog);
  cancel.addEventListener("click", close);
  ok.addEventListener("click", async () => {
    ok.disabled = true;
    const ids = group.get();
    try {
      const json = await api("PUT", url, { org_ids: ids });
      close();
      onSaved(json.orgs, ids);
      toast("Uloženo");
    } catch (e) { ok.disabled = false; toast(e.message, true); }
  });
};
