// Camp Planner — camp-wide TODO overview page. Thin host: parse the server-inlined JSON and
// hand it to the shared cpTodoList component (activity column + filter/sort enabled, filter
// state persisted in the URL hash). All rendering and mutation live in todo-list.js.
"use strict";

(function () {
  const mount = document.getElementById("cp-todos");
  const dataEl = document.getElementById("cp-todos-data");
  if (!mount || !dataEl) return;
  const DATA = JSON.parse(dataEl.textContent);

  window.cpTodoList({
    mount,
    todos: DATA.todos,
    orgs: DATA.orgs,
    activities: DATA.activities,
    // map the server url names to the component's (item = PATCH/DELETE; no create on the overview)
    urls: { item: DATA.urls.todoItem, activityDetail: DATA.urls.activityDetail },
    mayEdit: DATA.may_edit,
    showActivity: true,
    useHash: true,
    notesToggle: true,
  });
})();
