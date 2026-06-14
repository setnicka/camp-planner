// Camp Planner — timeline editor (loaded only when the user can edit).
//
// Split out of timeline.js: drag/resize existing slots, double-tap to add (with an
// activity-picker modal), tap-select + action bar to delete, undo/redo, an unsaved-
// changes list, and a batched PATCH save under the timeline_rev optimistic lock.
// timeline.js calls window.cpTimelineEdit(ctx) with the render context it shares.
"use strict";

window.cpTimelineEdit = function setupEditing(ctx) {
  const { EDIT, payload, camp, container, items, timeline, DAY_MIN, WINDOW_START, winStart, Y, Mo, D, ROLE_LABEL, roleHeading, fmtClock, mToDate, escapeHtml, applyHeights, segmentContent, segmentTitle, segmentBase } = ctx;
  const { el, csrf, swatch, openModal, chipGroup, keyList, toast, toastNext, plural } = window.cpDom;
  const pad = (n) => String(n).padStart(2, "0");
  const catById = Object.fromEntries(payload.categories.map((c) => [c.id, c]));

  let editing = false;
  let tempSeq = 0;
  let reloading = false;  // set before our own location.reload() so beforeunload stays quiet
  const reload = () => { reloading = true; location.reload(); };
  // Net batch sent on Save; kept in sync by every change's apply/revert.
  const moves = new Map();    // slot_id -> {start_at, end_at}  (existing slots repositioned)
  const retypes = new Map();  // slot_id -> role               (existing slots whose type changed)
  const deletes = new Set();  // slot_id                        (existing slots removed)
  const creates = new Map();  // item id (string) -> {activity_id, role, start_at, end_at}
  // One entry per user action (drag / resize / create / delete); each can undo()/redo().
  const history = [];
  const redoStack = [];
  let activitiesCache = null; // [{id, title, category_id}, …], lazy-loaded for the picker

  // Slots sliced across the window boundary render as several items; locking those
  // from drag avoids desyncing the pieces (delete still works, keyed by slot_id).
  const segCount = {};
  items.get().forEach((it) => {
    if (it.slotId != null) segCount[it.slotId] = (segCount[it.slotId] || 0) + 1;
  });
  const isLocked = (it) => it.slotId != null && segCount[it.slotId] > 1;
  // every rendered piece of a slot (a window-crossing slot has several rows)
  const segsOf = (slotId) => items.get({ filter: (it) => it.slotId === slotId });

  const toggleBtn = document.getElementById("cp-edit-toggle");
  const saveBtn = document.getElementById("cp-save");
  const undoBtn = document.getElementById("cp-undo");
  const redoBtn = document.getElementById("cp-redo");
  const changesBtn = document.getElementById("cp-changes");

  // --- time math (vis item <-> naive datetime string), mirrors services.timeline ---
  const relMin = (date) => Math.round((date.getTime() - winStart) / 60000); // 0..1440 from window open
  function absToNaive(absMin) {
    const dayOff = Math.floor(absMin / DAY_MIN);
    const inDay = absMin - dayOff * DAY_MIN;
    const dt = new Date(Y, Mo - 1, D + dayOff, Math.floor(inDay / 60), inDay % 60);
    return `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}T${pad(dt.getHours())}:${pad(dt.getMinutes())}`;
  }
  const absMinOf = (group, date) => Number(group) * DAY_MIN + WINDOW_START + relMin(date);
  function itemTimes(item) {
    return {
      start_at: absToNaive(absMinOf(item.group, item.start)),
      end_at: absToNaive(absMinOf(item.group, item.end)),
    };
  }

  // --- change log (undo / redo) ----------------------------------------------
  const snapshot = (id) => { const it = items.get(id); return it ? { start: it.start, end: it.end, group: it.group } : null; };
  const clockOf = (group, date) => fmtClock(absMinOf(group, date));
  const rangeLabel = (group, start, end) => `${clockOf(group, start)}–${clockOf(group, end)}`;
  // "<day>. HH:MM" for an absolute camp-minute — used to label a multi-row slot's range,
  // which a single group's HH:MM–HH:MM can't express.
  function dayClock(abs) {
    const i = Math.max(0, Math.min(payload.groups.length - 1, Math.floor((abs - WINDOW_START) / DAY_MIN)));
    const g = payload.groups[i];
    const dom = g && g.iso_date ? g.iso_date.slice(8).replace(/^0/, "") + ". " : "";
    return dom + fmtClock(abs);
  }
  const slotRangeLabel = (aStart, aEnd) => `${dayClock(aStart)} → ${dayClock(aEnd)}`;
  const hasPending = () => history.length > 0;

  // record() only logs (the caller has already applied the forward effect); undo()/redo()
  // re-apply the before/after state to both the items DataSet and the net batch maps.
  function record(change) { history.push(change); redoStack.length = 0; afterChange(); }
  function undo() { const c = history.pop(); if (c) { c.undo(); redoStack.push(c); afterChange(); } }
  function redo() { const c = redoStack.pop(); if (c) { c.redo(); history.push(c); afterChange(); } }
  function afterChange() { hideActionBar(); applyHeights(); refresh(); }

  function pluralChanges(n) {
    return `${n} ${plural(n, "změna", "změny", "změn")}`;
  }
  function refresh() {
    if (saveBtn) saveBtn.disabled = !history.length;
    if (undoBtn) undoBtn.disabled = !history.length;
    if (redoBtn) redoBtn.disabled = !redoStack.length;
    if (changesBtn) { changesBtn.hidden = !history.length; changesBtn.textContent = pluralChanges(history.length); }
    if (changesOpen) renderChangeList();
  }

  // numbered change rows, shared by the popover and the Save/Discard confirm dialog
  const changeRows = () => history.map((c, i) => el("div", { class: "cp-change-row" }, (i + 1) + ". " + c.label));

  // --- unsaved-changes list popover ------------------------------------------
  let changesOpen = false;
  const changesPanel = el("div", { class: "cp-changes-panel", hidden: true });
  document.body.append(changesPanel);
  const closeChanges = () => { changesOpen = false; changesPanel.hidden = true; };
  function renderChangeList() {
    if (!history.length) { closeChanges(); return; }
    changesPanel.replaceChildren(...changeRows());
  }
  function toggleChanges() {
    changesOpen = !changesOpen;
    if (!changesOpen || !history.length) { closeChanges(); return; }
    renderChangeList();
    changesPanel.hidden = false;
    const r = changesBtn.getBoundingClientRect();
    changesPanel.style.top = (r.bottom + 4) + "px";
    // right-align under the button, clamped on-screen using the panel's real width
    const left = Math.min(r.right - changesPanel.offsetWidth, window.innerWidth - changesPanel.offsetWidth - 4);
    changesPanel.style.left = Math.max(4, left) + "px";
  }
  document.addEventListener("click", (e) => {
    if (changesOpen && e.target !== changesBtn && !changesPanel.contains(e.target)) closeChanges();
  });

  // --- move / resize ---------------------------------------------------------
  // vis applies the visual move via callback(item); we sync the batch map + log a
  // change. A same-duration drag is "Přesunut"; a changed duration is "Změněna velikost".
  function onMove(item, callback) {
    if (isLocked(item)) {  // multi-row (window-crossing) slot: re-slice the whole slot from this one drag
      const change = lockedSlotEdit(item);  // toasts on out-of-range
      callback(null);                        // always cancel vis's single-piece move; we re-render instead
      if (change) { change.redo(); record(change); }
      return;
    }
    const id = item.id, key = String(id);
    const cur = items.get(id) || {};
    const title = cur._title || "slot";
    const before = snapshot(id);
    const after = { start: item.start, end: item.end, group: item.group };
    if (before && +before.start === +after.start && +before.end === +after.end &&
        String(before.group) === String(after.group)) {
      callback(item); return;  // dropped back where it started → not a change
    }
    const afterTimes = itemTimes(item);
    const verb = (before.end - before.start) === (after.end - after.start) ? "Přesunut" : "Změněna velikost";
    const label = `${verb} „${title}“: ${rangeLabel(before.group, before.start, before.end)}` +
                  ` → ${rangeLabel(after.group, after.start, after.end)}`;

    let change = null;
    if (creates.has(key)) {
      const spec = creates.get(key);
      const beforeTimes = { start_at: spec.start_at, end_at: spec.end_at };
      Object.assign(spec, afterTimes);
      change = {
        label,
        undo: () => { items.update({ id, ...before }); Object.assign(spec, beforeTimes); },
        redo: () => { items.update({ id, ...after }); Object.assign(spec, afterTimes); },
      };
    } else if (item.slotId != null) {
      const slotId = item.slotId;
      const prev = moves.has(slotId) ? moves.get(slotId) : null;
      moves.set(slotId, afterTimes);
      change = {
        label,
        undo: () => { items.update({ id, ...before }); if (prev) moves.set(slotId, prev); else moves.delete(slotId); },
        redo: () => { items.update({ id, ...after }); moves.set(slotId, afterTimes); },
      };
    }
    callback(item);              // apply the visual move first…
    if (change) record(change);  // …then log it + recompute heights against the new layout
  }

  // live time-in-box while dragging/resizing: rewrite the .ev-time text directly
  // (mutating item.content would kill the drag). Locked (multi-row) slots get the same
  // live feedback; they re-slice to the final layout on drop.
  const cssId = (id) => (window.CSS && CSS.escape) ? CSS.escape(String(id)) : String(id);
  let movingTimeEl = null;  // cached across a drag's many onMoving frames (same item id)
  function onMoving(item, callback) {
    callback(item);
    if (!movingTimeEl || movingTimeEl.dataset.id !== String(item.id) || !movingTimeEl.isConnected)
      movingTimeEl = container.querySelector('.ev-time[data-id="' + cssId(item.id) + '"]');
    if (movingTimeEl) movingTimeEl.textContent = rangeLabel(item.group, item.start, item.end);
  }

  // --- add (double-tap empty space) ------------------------------------------
  function onAdd(item, callback) {
    callback(null); // we manage our own placeholder item instead of vis's default
    const group = Number(item.group ?? payload.groups[0].id);
    let s = relMin(item.start) - 60, e = relMin(item.start) + 60; // ±1h around the tap
    if (s < 0) { e -= s; s = 0; }
    if (e > DAY_MIN) { s -= e - DAY_MIN; e = DAY_MIN; }
    if (s < 0) s = 0;
    const id = "new-" + (++tempSeq);
    items.add({
      id, group, start: mToDate(s), end: mToDate(e),
      content: '<div class="ev"><div class="ev-title">Nový blok…</div></div>',
      className: "cp-placeholder",
    });
    timeline.setSelection([]);
    openActivityModal({
      onConfirm: (activity, role) => bindNewSlot(id, group, s, e, activity, role),
      onCancel: () => items.remove(id),
    });
  }

  // Renderable vis-item fields derived from a segment object (the renderer's `_seg`). Shared
  // by persisted slots and pending creates, so both go through one rendering path; a create
  // (slot_id still null) gets the dashed `cp-new` class. `_base` is the class applyHeights toggles.
  function segData(seg, isCreate) {
    const base = segmentBase(seg) + (isCreate ? " cp-new" : "");
    return { role: seg.role, className: base, _base: base,
             content: segmentContent(seg), title: segmentTitle(seg) };
  }

  // --- multi-row (window-crossing) slot editing ------------------------------
  // A slot whose [start,end] crosses the daily window renders as several items, one per
  // row. We keep the slot's absolute [start,end] canonical (carried on every segment as
  // abs_start_min/abs_end_min) and, on a drag, re-derive the whole slot then re-slice it —
  // both halves move together. Mirrors services.timeline slicing and the mockup's
  // applySegmentEdit (data.js). Re-rendering swaps the slot's items wholesale.

  // Slice an absolute [start,end] into per-row segment objects, inheriting the slot's
  // metadata from `tmpl`. Clamped to real rows; returns [] if nothing lands on a row.
  function sliceSlot(absStart, absEnd, tmpl) {
    const segs = [];
    const lastRow = payload.groups.length - 1;
    const first = Math.max(0, Math.floor((absStart - WINDOW_START) / DAY_MIN));
    const last = Math.min(lastRow, Math.floor((absEnd - 1 - WINDOW_START) / DAY_MIN));
    for (let day = first; day <= last; day++) {
      const winLo = day * DAY_MIN + WINDOW_START, winHi = winLo + DAY_MIN;
      const lo = Math.max(absStart, winLo), hi = Math.min(absEnd, winHi);
      if (hi <= lo) continue;                         // slot doesn't reach this row's window
      segs.push({
        ...tmpl, day,
        abs_start_min: absStart, abs_end_min: absEnd,  // true slot range on every piece (for the HH:MM label)
        rel_start_min: lo - winLo, rel_end_min: hi - winLo,
        cont_back: lo > absStart + 0.5, cont_fwd: hi < absEnd - 0.5,
      });
    }
    return segs;
  }

  // A renderable vis item for one freshly-sliced segment (fresh string id; no DB meaning).
  function segItem(seg) {
    const id = "rs" + (++tempSeq);
    seg.idx = id;                                     // segmentContent keys its .ev-time on seg.idx
    return { id, group: seg.day, start: mToDate(seg.rel_start_min), end: mToDate(seg.rel_end_min),
             slotId: seg.slot_id, _seg: seg, _title: seg.title, ...segData(seg, false) };
  }

  // Map a drag/resize of ONE segment onto its owning slot, then re-slice. Returns a
  // change ({label, undo, redo}) to record, or null for a no-op / out-of-range edit.
  function lockedSlotEdit(item) {
    const slotId = item.slotId;
    const cur = items.get(item.id);
    const seg = cur._seg, title = cur._title || "slot";
    const winLo = seg.day * DAY_MIN + WINDOW_START;
    const dStart = Math.round(absMinOf(item.group, item.start) - (winLo + seg.rel_start_min));
    const dEnd = Math.round(absMinOf(item.group, item.end) - (winLo + seg.rel_end_min));
    const MIN = camp.snap_minutes;
    let s = seg.abs_start_min, e = seg.abs_end_min;   // baseline = the slot at render time
    if (dStart === dEnd) { s += dStart; e += dStart; }  // equal shift → MOVE the whole slot
    else {                                              // one edge → RESIZE that end; cut edges ignored
      if (dStart !== 0 && !seg.cont_back) s = Math.min(seg.abs_start_min + dStart, seg.abs_end_min - MIN);
      if (dEnd !== 0 && !seg.cont_fwd) e = Math.max(seg.abs_end_min + dEnd, seg.abs_start_min + MIN);
    }
    s = Math.round(s / MIN) * MIN; e = Math.round(e / MIN) * MIN;
    if (e <= s) e = s + MIN;
    if (s === seg.abs_start_min && e === seg.abs_end_min) return null;   // nothing changed (e.g. cut-edge drag)

    const oldItems = segsOf(slotId);
    const segs = sliceSlot(s, e, { ...oldItems[0]._seg });
    if (!segs.length) { toast("Mimo rozsah tábora.", true); return null; }
    const newItems = segs.map(segItem), newIds = newItems.map((it) => it.id);
    const oldIds = oldItems.map((it) => it.id);
    const newTimes = { start_at: absToNaive(s), end_at: absToNaive(e) };
    const prevMove = moves.has(slotId) ? moves.get(slotId) : null;
    const verb = dStart === dEnd ? "Přesunut" : "Změněna velikost";
    const label = `${verb} „${title}“: ${slotRangeLabel(seg.abs_start_min, seg.abs_end_min)} → ${slotRangeLabel(s, e)}`;
    return {
      label,
      redo: () => { items.remove(oldIds); items.add(newItems); moves.set(slotId, newTimes); segCount[slotId] = newIds.length; },
      undo: () => { items.remove(newIds); items.add(oldItems); if (prevMove) moves.set(slotId, prevMove); else moves.delete(slotId); segCount[slotId] = oldIds.length; },
    };
  }

  function bindNewSlot(id, group, sRel, eRel, activity, role) {
    const catKey = catById[activity.category_id]?.key ?? "_none";
    const sAbs = group * DAY_MIN + WINDOW_START + sRel, eAbs = group * DAY_MIN + WINDOW_START + eRel;
    const spec = { activity_id: activity.id, role, start_at: absToNaive(sAbs), end_at: absToNaive(eAbs) };
    // a pending create is a real segment with no slot_id yet, rendered like any other.
    const seg = {
      idx: id, title: activity.title, role, cat_key: catKey, activity_id: activity.id, slot_id: null,
      abs_start_min: sAbs, abs_end_min: eAbs, garants: [], helpers: [], attending: [],
      tag_ids: [], cont_back: false, cont_fwd: false,
    };
    const data = { id, group, start: mToDate(sRel), end: mToDate(eRel), slotId: null,
                   _seg: seg, _title: activity.title, ...segData(seg, true) };
    items.update(data);          // convert the placeholder into the real (pending) slot
    creates.set(id, spec);
    rememberRecent(activity.id);
    const when = `${fmtClock(sAbs)}–${fmtClock(eAbs)}`;
    record({
      label: `Vytvořen slot „${roleHeading(role, activity.title)}“: ${when}`,
      undo: () => { creates.delete(id); items.remove(id); },
      redo: () => { creates.set(id, spec); items.update(data); },
    });
  }

  // --- delete (action bar on the selected slot) ------------------------------
  function deleteSelected() {
    const [id] = timeline.getSelection();
    if (id == null) return;
    const snap = items.get(id);
    if (!snap) return;
    const key = String(id);
    const when = rangeLabel(snap.group, snap.start, snap.end);
    const title = snap._title || "slot";
    if (creates.has(key)) {
      const spec = creates.get(key);
      creates.delete(key); items.remove(id);
      record({
        label: `Smazán nový slot „${title}“: ${when}`,
        undo: () => { creates.set(key, spec); items.add(snap); },
        redo: () => { creates.delete(key); items.remove(id); },
      });
    } else if (snap.slotId != null) {
      const slotId = snap.slotId;
      const segs = segsOf(slotId);   // every row of a multi-row slot
      const ids = segs.map((it) => it.id);
      const label = snap._seg ? slotRangeLabel(snap._seg.abs_start_min, snap._seg.abs_end_min) : when;
      deletes.add(slotId); items.remove(ids);
      record({
        label: `Smazán slot „${title}“: ${label}`,
        undo: () => { deletes.delete(slotId); items.add(segs); },
        redo: () => { deletes.add(slotId); items.remove(ids); },
      });
    }
  }

  // --- change slot type (role) ----------------------------------------------
  // Re-render the given items with a new role (mutating their _seg). One item for a pending
  // create, every segment for an existing (possibly multi-day) slot.
  function rerenderRole(itemList, role) {
    itemList.forEach((it) => {
      if (!it._seg) return;
      it._seg.role = role;
      items.update({ id: it.id, ...segData(it._seg, it.slotId == null) });
    });
  }
  function changeSlotType(item, newRole) {
    const key = String(item.id), before = item.role || "main";
    if (newRole === before) return;
    const label = `Změněn typ „${item._title || "slot"}“: ${ROLE_LABEL[before]} → ${ROLE_LABEL[newRole]}`;
    if (creates.has(key)) {
      const spec = creates.get(key);
      const apply = (role) => { spec.role = role; rerenderRole([items.get(key)], role); };
      apply(newRole);
      record({ label, undo: () => apply(before), redo: () => apply(newRole) });
    } else if (item.slotId != null) {
      const slotId = item.slotId;
      const segs = () => segsOf(slotId);
      const prev = retypes.has(slotId) ? retypes.get(slotId) : undefined;  // undefined = no pending retype
      const apply = (role) => { retypes.set(slotId, role); rerenderRole(segs(), role); };
      apply(newRole);
      record({
        label,
        undo: () => { rerenderRole(segs(), before); if (prev === undefined) retypes.delete(slotId); else retypes.set(slotId, prev); },
        redo: () => apply(newRole),
      });
    }
  }

  // role-picker modal (chips, current role pre-selected); used from the edit-mode action bar
  function openSlotType(item) {
    const current = creates.has(String(item.id)) ? creates.get(String(item.id)).role : (item.role || "main");
    const roles = chipGroup(Object.entries(ROLE_LABEL), { selected: current });
    const cancel = el("button", { type: "button", class: "cp-cancel" }, "Zrušit");
    const ok = el("button", { type: "button", class: "cp-primary" }, "Uložit");
    const dialog = el("div", { class: "cp-modal cp-modal-wide" },
      el("div", { class: "cp-modal-head" }, "Typ slotu"),
      el("div", { class: "cp-pane" }, roles.node),
      el("div", { class: "cp-modal-foot" }, cancel, ok));
    const close = openModal(dialog);
    cancel.addEventListener("click", close);
    ok.addEventListener("click", () => { close(); changeSlotType(item, roles.get()); });
  }

  // --- floating action bar (available in view mode too: assign orgs / open detail) --
  const orgsBtn = el("button", { type: "button", class: "cp-tl-orgs" }, "Přiřadit orgy");
  const detailBtn = el("button", { type: "button", class: "cp-tl-detail" }, "ℹ️ Detail");
  const retypeBtn = el("button", { type: "button", class: "cp-tl-retype" }, "↺ Typ slotu");
  const delBtn = el("button", { type: "button", class: "cp-tl-del" }, "🗑 Smazat blok");
  const actionBar = el("div", { class: "cp-tl-actions", hidden: true }, orgsBtn, detailBtn, retypeBtn, delBtn);
  delBtn.addEventListener("click", deleteSelected);
  retypeBtn.addEventListener("click", () => {
    const [id] = timeline.getSelection();
    const it = id != null && items.get(id);
    if (it) openSlotType(it);
  });
  orgsBtn.addEventListener("click", () => {
    const [id] = timeline.getSelection();
    const it = id != null && items.get(id);
    if (it && it.slotId != null) openSlotOrgs(it);
  });
  detailBtn.addEventListener("click", () => {
    const [id] = timeline.getSelection();
    const it = id != null && items.get(id);
    const aid = it && it._seg && it._seg.activity_id;
    if (aid != null) location.href = EDIT.activityDetail.replace(/\d+$/, aid);  // swap the 0 sentinel
  });
  document.body.append(actionBar);

  function showActionBar() {
    requestAnimationFrame(() => {
      const sel = container.querySelector(".vis-item.vis-selected");
      if (!sel) return hideActionBar();
      const [id] = timeline.getSelection();
      const it = id != null && items.get(id);
      // edit mode = change type / delete; view mode = assign orgs / open detail
      orgsBtn.hidden = editing || !(it && it.slotId != null); // attendees need a saved slot id
      detailBtn.hidden = editing;
      retypeBtn.hidden = !editing;
      delBtn.hidden = !editing;
      const r = sel.getBoundingClientRect();
      actionBar.hidden = false;
      actionBar.style.left = Math.max(4, Math.min(r.left, window.innerWidth - actionBar.offsetWidth - 4)) + "px";
      actionBar.style.top = Math.max(4, r.top - actionBar.offsetHeight - 6) + "px";
    });
  }
  const hideActionBar = () => { actionBar.hidden = true; };

  timeline.on("select", (props) => {
    if (props.items.length) showActionBar(); else hideActionBar();
  });
  // drop the bar whenever interaction/focus moves away from the selected slot
  const dropSelection = () => { if (!actionBar.hidden) { hideActionBar(); timeline.setSelection([]); } };
  document.addEventListener("pointerdown", (e) => {
    if (!container.contains(e.target) && !actionBar.contains(e.target)) dropSelection();
  });
  window.addEventListener("blur", dropSelection);   // tab/window loses focus
  timeline.on("rangechange", hideActionBar);        // pan/zoom slides the slot out from under it

  // --- slot attendees (who staffs this block) --------------------------------
  // Shared dialog (cpSlotOrgsEdit) — same window as the activity detail page. The PUT is a
  // standalone commit (not part of the move/create/delete batch; doesn't touch timeline_rev);
  // on save we re-render the slot's segments and refresh the display-filter dim.
  function openSlotOrgs(item) {
    const slotId = item.slotId;
    window.cpSlotOrgsEdit({
      orgs: payload.orgs,
      selected: (item._seg && item._seg.attending) || [],
      url: EDIT.slotOrgs.replace(/0\/orgs$/, slotId + "/orgs"),
      onSaved: (_orgs, ids) => {
        segsOf(slotId).forEach((it) => {
          if (it._seg) {
            it._seg.attending = ids;
            items.update({ id: it.id, content: segmentContent(it._seg), title: segmentTitle(it._seg) });
          }
        });
        applyHeights();   // attendees changed → refresh the display filter's dim (e.g. an "attending:" filter)
      },
    });
  }

  // --- save (one PATCH; on success reload the authoritative state) -----------
  // force=true sends rev:null, which makes the server skip the optimistic-lock check
  // and overwrite whatever is there (used from the conflict dialog's "Přepsat").
  async function save(force) {
    if (!hasPending()) return;
    saveBtn.disabled = true;
    const body = {
      rev: force ? null : camp.rev,
      moves: [...moves.entries()].map(([slot_id, t]) => ({ slot_id, ...t })),
      creates: [...creates.values()],
      retypes: [...retypes.entries()].map(([slot_id, role]) => ({ slot_id, role })),
      deletes: [...deletes],
    };
    try {
      const resp = await fetch(EDIT.save, {
        method: "PATCH",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
        body: JSON.stringify(body),
      });
      const json = await resp.json().catch(() => ({}));
      if (resp.status === 409) { openConflict(); return; }
      if (!resp.ok || !json.ok) {
        toast(json.error || "Uložení selhalo.", true);
        saveBtn.disabled = false;
        return;
      }
      toastNext("Časový plán uložen");   // survives the reload below
      reload(); // simplest correct refresh: server re-slices + fresh rev
    } catch (_e) {
      toast("Chyba spojení při ukládání.", true);
      saveBtn.disabled = false;
    }
  }

  // shown when the save hit a stale-rev conflict: explain + offer the three ways out
  function openConflict() {
    saveBtn.disabled = false; // back to a decision; the save isn't in flight anymore
    const opts = el("ul", { class: "cp-conflict-opts" });
    ["Vrátit se k editaci (a změny případně porovnat v druhém tabu).",
     "Přepsat vzdálené změny svými (force).",
     "Zrušit moje změny a načíst vzdálené."].forEach((t) => opts.append(el("li", null, t)));
    const back = el("button", { type: "button", class: "cp-cancel" }, "Zpět k editaci");
    const force = el("button", { type: "button", class: "cp-primary cp-blue-btn" }, "Přepsat (force)");
    const discard = el("button", { type: "button", class: "cp-primary cp-warn-btn" }, "Zahodit moje a načíst");
    const dialog = el("div", { class: "cp-modal cp-modal-wide" },
      el("div", { class: "cp-modal-head cp-head-danger" }, "⚠️ Konflikt"),
      el("div", { class: "cp-pane" },
        el("p", null, "Někdo jiný mezitím uložil změny. Doporučení: načíst si změny ve druhém tabu a porovnat."),
        el("p", null, "Máte tyto možnosti:"), opts),
      el("div", { class: "cp-modal-foot" }, back, force, discard));
    const close = openModal(dialog);
    back.addEventListener("click", close);
    discard.addEventListener("click", () => { close(); reload(); });
    force.addEventListener("click", () => { close(); save(true); });
    back.focus();
  }

  // --- confirm dialog listing the pending changes (Save / Discard) -----------
  function openChangesConfirm({ question, confirmLabel, danger, onConfirm }) {
    const list = el("div", { class: "cp-modal-list" }, ...changeRows());
    const cancel = el("button", { type: "button", class: "cp-cancel" }, "Zpět k úpravám");
    const ok = el("button", { type: "button", class: "cp-primary" + (danger ? " cp-danger-btn" : "") }, confirmLabel);
    const dialog = el("div", { class: "cp-modal cp-modal-wide" },
      el("div", { class: "cp-modal-head" }, question),
      el("div", { class: "cp-pane" }, list),
      el("div", { class: "cp-modal-foot" }, cancel, ok));
    const close = openModal(dialog);   // Escape / backdrop = back to edit
    cancel.addEventListener("click", close);
    ok.addEventListener("click", () => { close(); onConfirm(); });
    ok.focus();
  }

  // --- activity picker modal -------------------------------------------------
  async function fetchActivities() {
    if (activitiesCache) return activitiesCache;
    const resp = await fetch(EDIT.activities, { headers: { "X-CSRFToken": csrf() } });
    const json = await resp.json().catch(() => ({}));
    activitiesCache = (json.activities || []).map((a) => ({ id: a.id, title: a.title, category_id: a.category_id }));
    return activitiesCache;
  }
  const RECENT_KEY = "cp-recent-activities:" + camp.slug;
  function recentIds() {
    try { return JSON.parse(localStorage.getItem(RECENT_KEY) || "[]"); } catch (_e) { return []; }
  }
  function rememberRecent(id) {
    const next = [id, ...recentIds().filter((x) => x !== id)].slice(0, 3);
    try { localStorage.setItem(RECENT_KEY, JSON.stringify(next)); } catch (_e) { /* ignore */ }
  }

  function openActivityModal({ onConfirm, onCancel }) {
    // picked stays undefined until an activity is chosen; any dismissal (Escape /
    // backdrop / Zrušit) closes with picked still undefined → onCancel.
    let picked;
    // slot type (role) — applies to both tabs (existing pick or freshly created); main default.
    const roles = chipGroup(Object.entries(ROLE_LABEL), { selected: "main" });
    const finish = (activity) => { if (activity) { picked = activity; close(); onConfirm(activity, roles.get()); } else close(); };
    const roleRow = el("div", { class: "cp-modal-role" }, el("span", { class: "cp-modal-role-label" }, "Typ slotu:"), roles.node);

    const search = el("input", { type: "text", class: "cp-modal-search", placeholder: "Hledat aktivitu…" });
    const list = el("div", { class: "cp-modal-list" });
    const nameInput = el("input", { type: "text", class: "cp-modal-name", placeholder: "Název nové aktivity" });
    // a new activity must have a category: colored chips (native <option> colors are
    // ignored by most browsers), first one selected by default.
    const noCats = payload.categories.length === 0;
    const cats = chipGroup(
      payload.categories.map((c) => [c.id, swatch(c.color), c.label]),
      { selected: noCats ? null : payload.categories[0].id });
    const catChips = cats.node;
    const createBtn = el("button", { type: "button", class: "cp-primary", disabled: noCats }, "Vytvořit a přidat");

    // keyboard-navigable existing-activity list (cpDom.keyList): arrows move, Enter picks
    const setRows = keyList(search);
    function renderList(query) {
      const all = activitiesCache || [];
      const q = query.trim();
      const recentSet = new Set(q ? [] : recentIds());
      let acts;
      if (q) {
        acts = window.cpFuzzy
          ? window.cpFuzzy.filter(q, all, (a) => a.title)               // diacritics-folded fuzzy
          : all.filter((a) => a.title.toLowerCase().includes(q.toLowerCase()));
      } else {
        const byId = Object.fromEntries(all.map((a) => [a.id, a]));     // only the recents path needs it
        const recent = [...recentSet].map((id) => byId[id]).filter(Boolean);
        acts = [...recent, ...all.filter((a) => !recentSet.has(a.id))];
      }
      if (!acts.length) { list.replaceChildren(el("div", { class: "cp-muted" }, "Nic nenalezeno.")); setRows([]); return; }
      const entries = acts.map((a) => {
        const cat = catById[a.category_id];
        return {
          el: el("button", { type: "button", class: "cp-modal-item" },
            swatch(cat?.color),
            el("span", null, a.title),
            recentSet.has(a.id) ? el("span", { class: "cp-modal-recent" }, "naposledy") : null),
          pick: () => finish(a),
        };
      });
      list.replaceChildren(...entries.map((e) => e.el));
      setRows(entries);
    }
    search.addEventListener("input", () => renderList(search.value));

    async function createActivity() {
      const title = nameInput.value.trim();
      if (!title) { nameInput.focus(); return; }
      const categoryId = cats.get();
      if (categoryId == null) { toast("Vyberte kategorii.", true); return; }
      createBtn.disabled = true;
      try {
        const resp = await fetch(EDIT.createActivity, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
          body: JSON.stringify({ title, category_id: categoryId }),
        });
        const json = await resp.json().catch(() => ({}));
        if (!resp.ok || !json.ok) { toast(json.error || "Vytvoření selhalo.", true); createBtn.disabled = false; return; }
        const a = { id: json.activity.id, title: json.activity.title, category_id: json.activity.category_id };
        if (activitiesCache) activitiesCache.unshift(a);
        finish(a);
        toast("Aktivita vytvořena");
      } catch (_e) { toast("Chyba spojení.", true); createBtn.disabled = false; }
    }
    createBtn.addEventListener("click", createActivity);
    nameInput.addEventListener("keydown", (e) => { if (e.key === "Enter") createActivity(); });

    // tabs
    const tabExisting = el("button", { type: "button", class: "cp-tab on" }, "Existující");
    const tabNew = el("button", { type: "button", class: "cp-tab" }, "Nová");
    const paneExisting = el("div", { class: "cp-pane" }, search, list);
    const paneNew = el("div", { class: "cp-pane", hidden: true }, nameInput, catChips, createBtn,
      noCats ? el("div", { class: "cp-modal-hint" }, "Nejprve vytvořte kategorii v nastavení akce.") : null);
    const selectTab = (newTab) => {
      tabExisting.classList.toggle("on", !newTab); tabNew.classList.toggle("on", newTab);
      paneExisting.hidden = newTab; paneNew.hidden = !newTab;
      (newTab ? nameInput : search).focus();
    };
    tabExisting.addEventListener("click", () => selectTab(false));
    tabNew.addEventListener("click", () => selectTab(true));

    const dialog = el("div", { class: "cp-modal" },
      el("div", { class: "cp-modal-tabs" }, tabExisting, tabNew),
      roleRow,
      paneExisting, paneNew,
      el("div", { class: "cp-modal-foot" },
        el("button", { type: "button", class: "cp-cancel" }, "Zrušit")));
    dialog.querySelector(".cp-cancel").addEventListener("click", () => finish(null));
    const close = openModal(dialog, () => { if (picked === undefined) onCancel(); });

    fetchActivities().then(() => renderList("")).catch(() => renderList(""));
    search.focus();
  }

  // --- edit-mode toggle ------------------------------------------------------
  function setEditing(on) {
    editing = on;
    container.classList.toggle("cp-editing", on);
    toggleBtn.classList.toggle("on", on);
    if (toggleBtn.parentNode) toggleBtn.parentNode.classList.toggle("editing", on);
    toggleBtn.textContent = on ? "Zrušit" : "✏️ Upravit sloty a časy";
    for (const b of [saveBtn, undoBtn, redoBtn, changesBtn]) if (b) b.hidden = !on;
    if (!on) { hideActionBar(); timeline.setSelection([]); closeChanges(); }
    // No per-item `editable`: that overrides itemsAlwaysDraggable and would force a
    // select-first step. The global editable + itemsAlwaysDraggable make every box
    // drag/resize directly (matching the mock); multi-segment slots are guarded in onMove.
    // Only attach the callbacks when enabling — vis rejects `undefined` for them, and with
    // editable:false they never fire anyway, so there's no need to clear them on exit.
    const opts = {
      editable: on ? { add: true, updateTime: true, updateGroup: true, remove: false, overrideItems: false } : false,
      itemsAlwaysDraggable: on ? { item: true, range: true } : { item: false, range: false },
    };
    if (on) { opts.onMove = onMove; opts.onMoving = onMoving; opts.onAdd = onAdd; }
    timeline.setOptions(opts);
    // vis bakes editability into each item's DOM at render time, so re-add the program
    // items to make them pick up the new editable/itemsAlwaysDraggable options (per the mock).
    const ids = items.getIds({ filter: (it) => it._base != null });
    const data = items.get(ids);
    items.remove(ids);
    items.add(data);
    refresh();
  }

  // Revert every change in place (each is invertible) and leave edit mode — no reload.
  function discardChanges() {
    while (history.length) history.pop().undo();
    redoStack.length = 0;
    hideActionBar();
    applyHeights();
    setEditing(false);
  }
  toggleBtn.addEventListener("click", () => {
    if (editing && hasPending()) {
      openChangesConfirm({
        question: "Zahodit tyto změny?", confirmLabel: "Zahodit", danger: true,
        onConfirm: discardChanges,
      });
      return;
    }
    setEditing(!editing);
  });
  if (saveBtn) saveBtn.addEventListener("click", () => {
    if (hasPending()) openChangesConfirm({ question: "Uložit tyto změny?", confirmLabel: "Uložit", onConfirm: save });
  });
  if (undoBtn) undoBtn.addEventListener("click", undo);
  if (redoBtn) redoBtn.addEventListener("click", redo);
  if (changesBtn) changesBtn.addEventListener("click", toggleChanges);
  document.addEventListener("keydown", (e) => {
    if (!editing || !(e.ctrlKey || e.metaKey)) return;
    const k = e.key.toLowerCase();
    if (k === "z" && !e.shiftKey) { e.preventDefault(); undo(); }
    else if (k === "y" || (k === "z" && e.shiftKey)) { e.preventDefault(); redo(); }
  });
  window.addEventListener("beforeunload", (e) => {
    if (!reloading && hasPending()) { e.preventDefault(); e.returnValue = ""; }
  });
};
