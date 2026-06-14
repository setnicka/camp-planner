// Camp Planner — camp settings page (camp_edit.html).
//
// Wires the admin-only "Smazat akci" button to DELETE /api/camps/<slug> via cpDom.api. The
// button is server-rendered disabled (with an explaining title) when the camp still has
// activities, so this only ever runs for a deletable camp. Confirms first; on success it
// queues a toast that survives the redirect to the camp list, on failure it surfaces the
// server message (e.g. lost admin rights, or activities added meanwhile).
"use strict";

(function () {
  const root = document.querySelector("[data-camp-settings]");
  if (!root) return;
  const { api, toast, toastNext } = window.cpDom;
  const btn = root.querySelector("[data-delete-camp]");
  if (!btn) return;

  btn.addEventListener("click", async () => {
    const name = root.dataset.campName;
    if (!window.confirm(`Opravdu trvale smazat akci „${name}“? Tuto akci nelze vrátit.`)) return;
    btn.disabled = true;
    try {
      await api("DELETE", root.dataset.deleteUrl);
      toastNext("Akce byla smazána.");      // survives the navigation below
      window.location.href = root.dataset.redirect;
    } catch (err) {
      btn.disabled = false;
      toast(err.message, true);
    }
  });
})();
