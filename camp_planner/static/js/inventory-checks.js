// Camp Planner: inventory checks (/inventory/checks).
//
// The check lifecycle lives here: start one, finish or cancel it, and browse the finished
// ones. Its progress shows on the overview, box by box, where the recording happens.
"use strict";

(function () {
  const mount = document.getElementById("cp-inventory");
  const dataEl = document.getElementById("cp-inventory-data");
  if (!mount || !dataEl) return;

  const { el, api, formModal, toast, toastNext, withId } = window.cpDom;
  const inv = window.cpInventory;
  const DATA = JSON.parse(dataEl.textContent);
  const U = DATA.urls;
  const mayEdit = DATA.may_edit;
  const active = DATA.active_check;

  const totals = () => inv.sumProgress(DATA.progress);

  function startCheck() {
    const name = el("input", { type: "text", maxLength: 255, class: "cp-modal-name",
                               value: "Inventura " + new Date().getFullYear() });
    formModal({
      title: "Zahájit inventuru",
      pane: el("div", { class: "cp-pane" },
        el("label", { class: "cp-field-label" }, "Název"), name,
        el("div", { class: "cp-field-hint" },
          "Pod tímto názvem se inventura ukládá do historie a podepisuje poznámky u věcí.")),
      okLabel: "Zahájit",
      onSubmit: async (close) => {
        await api("POST", U.checks, { name: name.value.trim() });
        close();
        toastNext("Inventura zahájena.");
        location.reload();
      },
    });
  }

  // What completion would write and what is still open, from the preview rather than the
  // page's numbers: colleagues keep writing while the page is open.
  function previewPane({ summary, progress }) {
    const { checked, total } = inv.sumProgress(progress);
    const left = progress.filter((p) => p.total > p.checked);
    const lines = left.slice(0, 10)
      .map((p) => el("li", null, `${p.box.name}: zbývá ${p.total - p.checked} z ${p.total}`));
    const more = left.length - lines.length;
    if (more > 0) {
      lines.push(el("li", { class: "cp-muted" },
        "… a " + inv.countLabel(more, "další krabice", "další krabice", "dalších krabic")));
    }
    return [
      el("p", null, `Zkontrolováno ${checked} z ${total}.`),
      el("p", { class: "cp-muted" }, "Dopady: " + (inv.impactText(summary) || "žádné")),
      el("p", null, "Zápisy se propíšou do věcí. Dokončenou inventuru nelze vrátit."),
      checked ? null : el("p", { class: "cp-danger-text" },
        "Nic nebylo zkontrolováno – spíš inventuru zrušit."),
      lines.length ? el("p", { class: "cp-field-hint" },
        "Nezkontrolované věci zůstanou beze změny.") : null,
      lines.length ? el("ul", { class: "cp-inv-left" }, ...lines) : null,
    ].filter(Boolean);
  }

  function completeCheck() {
    const pane = el("div", { class: "cp-pane" });
    const { ok } = formModal({
      title: "Dokončit inventuru",
      pane,
      okLabel: "Dokončit",
      onSubmit: async (close) => {
        await api("POST", withId(U.checkComplete, active.id));
        close();
        toastNext("Inventura dokončena.");
        location.href = inv.checkUrl(U, active.id);
      },
    });
    // Not blind: the dialog exists to show what completion will write, so OK waits for it.
    function load() {
      ok.disabled = true;
      pane.replaceChildren(el("p", { class: "cp-muted" }, "Načítám…"));
      api("GET", withId(U.checkPreview, active.id))
        .then((j) => { pane.replaceChildren(...previewPane(j)); ok.disabled = false; })
        .catch((e) => pane.replaceChildren(el("p", { class: "cp-danger-text" }, e.message),
          el("button", { type: "button", class: "cp-mini", onclick: load }, "Zkusit znovu")));
    }
    load();
  }

  async function cancelCheck() {
    // Counted now, not at page load: colleagues keep writing while the page is open.
    let checked;
    try {
      checked = inv.sumProgress((await api("GET", withId(U.checkPreview, active.id))).progress).checked;
    } catch (e) {
      toast(e.message, true);
      return;
    }
    const drop = checked
      ? " Zahodí se " + (checked >= 5 ? "všech " : "")
        + inv.countLabel(checked, "zápis", "zápisy", "zápisů") + "."
      : "";
    if (!window.confirm("Zrušit inventuru?" + drop)) return;
    try {
      await api("DELETE", withId(U.checkItem, active.id));
      toastNext("Inventura zrušena.");
      location.reload();
    } catch (e) { toast(e.message, true); }
  }

  function activeSection() {
    if (!active) return null;
    const { checked, total } = totals();
    return el("section", null,
      inv.checkBar(active.name, checked, total, null,
        mayEdit ? inv.actionGroup([
          { label: "Dokončit inventuru", onClick: completeCheck },
          { label: "Zrušit inventuru", danger: true, onClick: cancelCheck },
        ]) : null),
      el("p", { class: "cp-muted" }, "Zapisuje se v jednotlivých krabicích – co ještě zbývá, ukazuje ",
        el("a", { href: U.overview }, "přehled skladu"), "."));
  }

  function historySection() {
    if (!DATA.checks.length) {
      return el("p", { class: "cp-muted" }, "Zatím neproběhla žádná inventura.");
    }
    return el("section", null,
      el("h2", null, "Proběhlé inventury"),
      el("ul", { class: "cp-inv-checks" }, ...DATA.checks.map((c) => {
        return el("li", null,
          el("a", { href: inv.checkUrl(U, c.id) }, c.name),
          el("span", { class: "cp-muted" }, inv.fmtDate(c.completed_at)),
          el("span", { class: "cp-muted" }, inv.summaryText(c.summary)));
      })));
  }

  mount.replaceChildren(...[
    el("div", { class: "cp-inv-head" }, el("h1", null, "Inventury")),
    activeSection(),
    !active && mayEdit
      ? el("button", { type: "button", class: "cp-add", onclick: startCheck },
          "+ Zahájit inventuru")
      : null,
    historySection(),
  ].filter(Boolean));
})();
