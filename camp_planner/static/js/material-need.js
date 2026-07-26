// Camp Planner — shared material-need dialog (amount / unit / note).
//
// Shared material-need modal (activity detail + materials overview).
// Edit mode (default) PATCHes `url`; create mode (method: "POST") posts the fields
// plus `extraBody` (e.g. {material_id}). Calls onSaved(); toasts success / failure.
// Exposed as window.cpMaterialNeedEdit; load after dom.js.
"use strict";

window.cpMaterialNeedEdit = function ({ title, need = {}, defaultUnit, url, method = "PATCH",
                                        extraBody, okLabel, onSaved }) {
  const { el, api, formModal, toast } = window.cpDom;
  const amount = el("input", { type: "number", step: "any", class: "cp-num", placeholder: "množství" });
  if (need.amount != null) amount.value = need.amount;
  const unit = el("input", { type: "text", class: "cp-need-unit", placeholder: defaultUnit || "jednotka" });
  unit.value = need.unit || "";
  const note = el("input", { type: "text", class: "cp-act-textarea" });
  note.value = need.note || "";
  formModal({
    title,
    okLabel: okLabel || "Uložit",
    pane: el("div", { class: "cp-pane" },
      el("label", { class: "cp-field-label" }, "Množství a jednotka"),
      el("div", { class: "cp-need-amount-row" }, amount, unit),
      el("div", { class: "cp-field-hint" }, "Jednotku zadej jen pokud se liší od výchozí."),
      el("label", { class: "cp-field-label" }, "Poznámka"), note),
    onSubmit: async (close) => {
      const body = {
        ...extraBody,
        amount: amount.value === "" ? null : Number(amount.value),
        unit: unit.value || null,
        note: note.value || null,
      };
      const json = await api(method, url, body);
      close();
      onSaved(json.need);
      toast("Uloženo");
    },
  });
  amount.focus();
};
