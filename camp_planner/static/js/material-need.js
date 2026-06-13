// Camp Planner — shared "edit material need" dialog.
//
// One modal for editing a material need's amount / unit / note, used by both the activity
// detail page and the camp-wide materials overview so the two stay identical. PATCHes the
// given url and hands the updated need to onSaved(); toasts success / failure. Exposed as
// window.cpMaterialNeedEdit; load after dom.js.
"use strict";

window.cpMaterialNeedEdit = function ({ title, need, defaultUnit, url, onSaved }) {
  const { el, api, openModal, toast } = window.cpDom;
  const amount = el("input", { type: "number", step: "any", class: "cp-num", placeholder: "množství" });
  if (need.amount != null) amount.value = need.amount;
  const unit = el("input", { type: "text", class: "cp-need-unit", placeholder: defaultUnit || "jednotka" });
  unit.value = need.unit || "";
  const note = el("input", { type: "text", class: "cp-act-textarea" });
  note.value = need.note || "";
  const cancel = el("button", { type: "button", class: "cp-cancel" }, "Zrušit");
  const ok = el("button", { type: "button", class: "cp-primary" }, "Uložit");
  const dialog = el("div", { class: "cp-modal cp-modal-wide" },
    el("div", { class: "cp-modal-head" }, title),
    el("div", { class: "cp-pane" },
      el("label", { class: "cp-field-label" }, "Množství a jednotka"),
      el("div", { class: "cp-need-amount-row" }, amount, unit),
      el("div", { class: "cp-field-hint" }, "Jednotku zadej jen pokud se liší od výchozí."),
      el("label", { class: "cp-field-label" }, "Poznámka"), note),
    el("div", { class: "cp-modal-foot" }, cancel, ok));
  const close = openModal(dialog);
  cancel.addEventListener("click", close);
  ok.addEventListener("click", async () => {
    ok.disabled = true;
    const body = {
      amount: amount.value === "" ? null : Number(amount.value),
      unit: unit.value || null,
      note: note.value || null,
    };
    try {
      const json = await api("PATCH", url, body);
      close();
      onSaved(json.need);
      toast("Uloženo");
    } catch (e) { ok.disabled = false; toast(e.message, true); }
  });
  amount.focus();
};
