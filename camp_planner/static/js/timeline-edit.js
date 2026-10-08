// Camp Planner: timeline editor (loaded only when the user can edit).
//
// Split out of timeline.js: drag/resize existing slots, double-tap to add (with an
// activity-picker modal), tap-select + action bar to edit, duplicate or delete, undo/redo,
// an unsaved-changes list, and a batched PATCH save under the timeline_rev optimistic lock.
// timeline.js calls window.cpTimelineEdit(ctx) with the render context it shares.
"use strict";

window.cpTimelineEdit = function setupEditing(ctx) {
  const { EDIT, payload, camp, container, items, timeline, DAY_MIN, WINDOW_START, winStart, Y, Mo, D, ROLE_LABEL, roleHeading, fmtClock, dayName, mToDate, applyHeights, segmentContent, segmentBase, rehydrate, selectItem, clearSelection, openDetail, busy, setBarActions, setPending, showBar, hideBar } = ctx;
  const { el, api, withId, canHover, openModal, formModal, chipGroup, toast, toastNext, plural } = window.cpDom;
  const pad = (n) => String(n).padStart(2, "0");
  const catById = Object.fromEntries(payload.categories.map((c) => [c.id, c]));

  let editing = false;
  let tempSeq = 0;
  let reloading = false;  // set before our own location.reload() so beforeunload stays quiet
  const reload = () => { reloading = true; location.reload(); };
  // Net batch sent on Save; kept in sync by every change's apply/revert.
  const moves = new Map();    // slot_id -> {start_at, end_at}  (existing slots repositioned)
  const updates = new Map();  // slot_id -> {role?, org_ids?, override_name?}
  const deletes = new Set();  // slot_id                        (existing slots removed)
  const creates = new Map();  // item id (string) -> {activity_id, role, start_at, end_at, …}
  // One entry per user action, each with undo()/redo() and `of`, its slot's slotKey.
  const history = [];
  const redoStack = [];

  // Slots sliced across the window boundary render as several items; locking those
  // from drag avoids desyncing the pieces (delete still works, keyed by slot_id).
  const segCount = {};
  function recomputeSegCount() {
    for (const k in segCount) delete segCount[k];
    items.get().forEach((it) => {
      if (it.slotId != null) segCount[it.slotId] = (segCount[it.slotId] || 0) + 1;
    });
  }
  recomputeSegCount();   // re-run after a save: a slot's segmentation can change
  const isLocked = (it) => it.slotId != null && segCount[it.slotId] > 1;
  // every rendered piece of a slot (a window-crossing slot has several rows)
  const segsOf = (slotId) => items.get({ filter: (it) => it.slotId === slotId });

  const toggleBtn = document.getElementById("cp-edit-toggle");
  const saveBtn = document.getElementById("cp-save");
  const undoBtn = document.getElementById("cp-undo");
  const redoBtn = document.getElementById("cp-redo");
  const changesBtn = document.getElementById("cp-changes");
  const toggleLabel = toggleBtn.innerHTML;   // carries the part a phone hides
  const frame = container.closest(".cp-tl-frame");   // toolbar + grid

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
  // An absolute range in text: "po 3. 8. 16:45–18:45", across rows "po 3. 8. 22:00 – út 4. 8.
  // 02:00". A single-row range leaves out its day when that is `skipDay`.
  const rowOf = (abs) => Math.max(0, Math.min(payload.groups.length - 1, Math.floor((abs - WINDOW_START) / DAY_MIN)));
  function spanLabel(s, e, skipDay) {
    const r = rowOf(s), rEnd = rowOf(e - 1);   // the end's row holds e - 1
    if (r !== rEnd) return `${dayName(r)} ${fmtClock(s)} – ${dayName(rEnd)} ${fmtClock(e)}`;
    return `${r === skipDay ? "" : dayName(r) + " "}${fmtClock(s)}–${fmtClock(e)}`;
  }
  // the target's day only when it differs from the source's
  const moveLabel = ([s0, e0], [s1, e1]) =>
    `${spanLabel(s0, e0)} → ${spanLabel(s1, e1, rowOf(s0) === rowOf(e0 - 1) ? rowOf(s0) : null)}`;
  // Changes of one kind to one slot share a fold key; the list shows their net change as one
  // row, or none.
  const nameOf = (item) => item._seg ? item._seg.override_name || item._seg.title : "slot";
  const slotKey = (item) => creates.has(String(item.id)) ? "new:" + item.id : "slot:" + item.slotId;
  const moveFold = (key, title, from, to) => ({
    key: "move:" + key, from, to,
    same: (a, b) => a[0] === b[0] && a[1] === b[1],
    label: (a, b) => `${b[1] - b[0] === a[1] - a[0] ? "Přesunut" : "Změněna velikost"} „${title}“: ${moveLabel(a, b)}`,
  });
  const hasPending = () => foldedChangeLines().length > 0;

  // record() only logs (the caller has already applied the forward effect); undo()/redo()
  // re-apply the before/after state to both the items DataSet and the net batch maps.
  function record(change) { history.push(change); redoStack.length = 0; afterChange(); }
  function undo() { const c = history.pop(); if (c) { c.undo(); redoStack.push(c); afterChange(); } }
  function redo() { const c = redoStack.pop(); if (c) { c.redo(); history.push(c); afterChange(); } }
  function afterChange() { showBar(); applyHeights(); refresh(); }   // the bar follows its slot

  function pluralChanges(n) {
    return `${n} ${plural(n, "změna", "změny", "změn")}`;
  }
  function refresh() {
    const lines = foldedChangeLines(), n = lines.length;
    if (saveBtn) saveBtn.disabled = !n;
    if (undoBtn) undoBtn.disabled = !history.length;
    if (redoBtn) redoBtn.disabled = !redoStack.length;
    if (changesBtn) { changesBtn.hidden = !n; changesBtn.textContent = pluralChanges(n); }
    if (changesOpen) renderChangeList(lines);
  }

  // A new slot deleted again leaves no row at all; a deleted saved slot only its delete.
  function gone(c) {
    const [kind, id] = c.of.split(":");
    return kind === "new" ? !creates.has(id) : deletes.has(Number(id)) && !c.deleting;
  }
  // The change list's rows, folded; they also drive the counter and whether anything is pending.
  function foldedChangeLines() {
    const rows = [];
    const byKey = new Map();    // fold.key -> its row
    for (const c of history) {
      if (gone(c)) continue;
      if (!c.fold) { rows.push(c.label); continue; }
      const seen = byKey.get(c.fold.key);
      if (seen) seen.last = c.fold;
      else { const row = { first: c.fold, last: c.fold }; byKey.set(c.fold.key, row); rows.push(row); }
    }
    return rows.flatMap((r) => typeof r === "string" ? [r]
      : r.first.same(r.first.from, r.last.to) ? [] : [r.first.label(r.first.from, r.last.to)]);
  }
  // Numbered rows, shared by the popover and the Save/Discard confirm dialog.
  function changeRows(lines = foldedChangeLines()) {
    return lines.map((txt, i) => el("div", { class: "cp-change-row" },
      el("span", { class: "cp-change-n" }, (i + 1) + "."), el("span", null, txt)));
  }

  // --- unsaved-changes list popover ------------------------------------------
  let changesOpen = false;
  const changesPanel = el("div", { class: "cp-changes-panel", hidden: true });
  document.body.append(changesPanel);
  const closeChanges = () => { changesOpen = false; changesPanel.hidden = true; };
  function renderChangeList(lines = foldedChangeLines()) {
    if (!lines.length) { closeChanges(); return; }
    changesPanel.replaceChildren(...changeRows(lines));
  }
  function toggleChanges() {
    changesOpen = !changesOpen;
    if (!changesOpen || !hasPending()) { closeChanges(); return; }
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
  // vis applies the visual move via callback(item); we sync the batch map + log a change.
  function onMove(item, callback) {
    dropGhost();
    showBar();   // back after the drag hid it, also when nothing changed; it waits a frame for vis
    if (isLocked(item)) {  // multi-row (window-crossing) slot: re-slice the whole slot from this one drag
      const change = lockedSlotEdit(item);  // toasts on out-of-range
      callback(null);                        // always cancel vis's single-piece move; we re-render instead
      if (change) { change.redo(); record(change); }
      return;
    }
    const id = item.id, key = String(id);
    const cur = items.get(id) || {};
    const seg = cur._seg;
    const title = nameOf(cur);
    const before = snapshot(id);
    const after = { start: item.start, end: item.end, group: item.group };
    if (before && +before.start === +after.start && +before.end === +after.end &&
        String(before.group) === String(after.group)) {
      callback(item); return;  // dropped back where it started → not a change
    }
    const afterTimes = itemTimes(item);
    // Re-render the box from the new time: its .ev-time and the card's clock derive from the
    // segment's abs range, so rebuild it from a copy with the new range.
    const afterSeg = seg && { ...seg, day: Number(after.group),
      abs_start_min: absMinOf(after.group, after.start), abs_end_min: absMinOf(after.group, after.end),
      rel_start_min: relMin(after.start), rel_end_min: relMin(after.end) };
    const afterRender = afterSeg
      ? { _seg: afterSeg, content: segmentContent(afterSeg) } : {};
    const beforeRender = seg ? { _seg: seg, content: cur.content } : {};
    const of = slotKey(cur);
    const fold = moveFold(of, title,
      [absMinOf(before.group, before.start), absMinOf(before.group, before.end)],
      [absMinOf(after.group, after.start), absMinOf(after.group, after.end)]);

    let change = null;
    if (creates.has(key)) {
      const spec = creates.get(key);
      const beforeTimes = { start_at: spec.start_at, end_at: spec.end_at };
      Object.assign(spec, afterTimes);
      change = {
        of, fold,
        undo: () => { items.update({ id, ...before, ...beforeRender }); Object.assign(spec, beforeTimes); },
        redo: () => { items.update({ id, ...after, ...afterRender }); Object.assign(spec, afterTimes); },
      };
    } else if (item.slotId != null) {
      const slotId = item.slotId;
      const prev = moves.has(slotId) ? moves.get(slotId) : null;
      moves.set(slotId, afterTimes);
      change = {
        of, fold,
        undo: () => { items.update({ id, ...before, ...beforeRender }); if (prev) moves.set(slotId, prev); else moves.delete(slotId); },
        redo: () => { items.update({ id, ...after, ...afterRender }); moves.set(slotId, afterTimes); },
      };
    }
    callback(item);                              // apply the visual move first…
    if (change) { items.update({ id, ...afterRender }); record(change); }  // …refresh box and card data, then log
  }

  // live time-in-box while dragging/resizing: rewrite the .ev-time text directly
  // (mutating item.content would kill the drag). Locked (multi-row) slots get the same
  // live feedback; they re-slice to the final layout on drop.
  const cssId = (id) => (window.CSS && CSS.escape) ? CSS.escape(String(id)) : String(id);
  let movingTimeEl = null;  // cached across a drag's many onMoving frames (same item id)
  // A hatched copy of the box stays where the drag started until the drop. A plain node in the
  // box's row, not a vis item, so it can't push the other boxes into new lanes.
  let ghost = null;
  function dropGhost() { ghost?.remove(); ghost = null; }
  timeline.on("rangechange", dropGhost);   // a pan or zoom mid-drag would leave it misplaced
  // the browser taking a drag over (a long-press menu, a scroll) ends it without onMove
  container.addEventListener("pointercancel", () => { if (ghost) { dropGhost(); showBar(); } });
  function onMoving(item, callback) {
    const idStr = String(item.id);
    if (!movingTimeEl || movingTimeEl.dataset.id !== idStr || !movingTimeEl.isConnected) {
      movingTimeEl = container.querySelector('.ev-time[data-id="' + cssId(item.id) + '"]');
    }
    const box = movingTimeEl?.closest(".vis-item");
    if (!ghost && box) {   // first frame, before the callback: the box is still where it started
      // held from the row's bottom, as vis lays its lanes, so it stays put while the row restacks
      const { transform, width } = box.style;
      const bottom = box.parentNode.offsetHeight - box.offsetTop - box.offsetHeight;
      ghost = el("div", { class: "vis-item cp-ghost" });
      Object.assign(ghost.style, { transform, width, top: "auto", bottom: bottom + "px",
        height: box.offsetHeight + "px" });
      box.before(ghost);
    }
    callback(item);
    if (movingTimeEl) {
      movingTimeEl.textContent = `${fmtClock(absMinOf(item.group, item.start))}–${fmtClock(absMinOf(item.group, item.end))}`;
    }
    hideBar();   // the bar and card stay behind as the box moves; back on drop
  }

  // --- add (double-tap empty space) ------------------------------------------
  function onAdd(item, callback) {
    callback(null); // we manage our own placeholder item instead of vis's default
    const group = Number(item.group ?? payload.groups[0].id);
    let s = relMin(item.start) - 60, e = relMin(item.start) + 60; // ±1h around the tap
    if (s < 0) { e -= s; s = 0; }
    if (e > DAY_MIN) { s -= e - DAY_MIN; e = DAY_MIN; }
    if (s < 0) s = 0;
    addSlot(group, s, e);
  }
  // A placeholder over [s, e] of the row while the activity picker is open. From the keyboard's
  // N, `fromId` is the slot it came from: the new slot is selected once made, else that again.
  function addSlot(group, s, e, fromId) {
    const id = "new-" + (++tempSeq);
    items.add({
      id, group, start: mToDate(s), end: mToDate(e),
      content: '<div class="ev"><div class="ev-title">Nový blok…</div></div>',
      className: "cp-placeholder",
    });
    clearSelection();
    window.cpActivityPicker({   // shared two-tab picker (activity-picker.js)
      activitiesUrl: EDIT.activities, createUrl: EDIT.createActivity,
      campSlug: camp.slug, categories: payload.categories, roleLabels: ROLE_LABEL,
      onConfirm: (activity, role) => { bindNewSlot(id, group, s, e, activity, role); if (fromId != null) selectItem(id); },
      onCancel: () => { items.remove(id); if (fromId != null && items.get(fromId)) selectItem(fromId); },
    });
  }

  // Renderable vis-item fields derived from a segment object (the renderer's `_seg`). Shared
  // by persisted slots and pending creates, so both go through one rendering path.
  // `_base` is the class applyHeights builds on.
  function segData(seg) {
    const base = segmentBase(seg);
    return { role: seg.role, className: base, _base: base,
             content: segmentContent(seg) };
  }

  // --- multi-row (window-crossing) slot editing ------------------------------
  // A slot whose [start,end] crosses the daily window renders as several items, one per
  // row. We keep the slot's absolute [start,end] canonical (carried on every segment as
  // abs_start_min/abs_end_min) and, on a drag, re-derive the whole slot then re-slice it:
  // both halves move together. Mirrors services.timeline slicing and the mockup's
  // applySegmentEdit (data.js). Re-rendering swaps the slot's items wholesale.

  // Slice an absolute [start,end] into per-row segment objects, inheriting the slot's
  // metadata from `tmpl`. Clamped to real rows; returns [] if nothing lands on a row.
  function sliceSlot(absStart, absEnd, tmpl) {
    const segs = [];
    for (let day = rowOf(absStart), last = rowOf(absEnd - 1); day <= last; day++) {
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
             slotId: seg.slot_id, _seg: seg, ...segData(seg) };
  }

  // Map a drag/resize of ONE segment onto its owning slot, then re-slice. Returns a
  // change to record, or null for a no-op / out-of-range edit.
  function lockedSlotEdit(item) {
    const slotId = item.slotId;
    const cur = items.get(item.id);
    const seg = cur._seg, title = nameOf(cur);
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

    if (s < WINDOW_START || e > WINDOW_START + payload.groups.length * DAY_MIN) {
      toast("Mimo rozsah tábora.", true);
      return null;
    }
    const oldItems = segsOf(slotId);
    const segs = sliceSlot(s, e, { ...oldItems[0]._seg });
    const newItems = segs.map(segItem), newIds = newItems.map((it) => it.id);
    const oldIds = oldItems.map((it) => it.id);
    const newTimes = { start_at: absToNaive(s), end_at: absToNaive(e) };
    const prevMove = moves.has(slotId) ? moves.get(slotId) : null;
    return {
      of: "slot:" + slotId,
      fold: moveFold("slot:" + slotId, title, [seg.abs_start_min, seg.abs_end_min], [s, e]),
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
                   _seg: seg, ...segData(seg) };
    items.update(data);          // convert the placeholder into the real (pending) slot
    creates.set(id, spec);
    const when = spanLabel(sAbs, eAbs);
    record({
      of: "new:" + id,
      label: `Vytvořen slot „${activity.title}“: ${when}`,
      undo: () => { creates.delete(id); items.remove(id); },
      redo: () => { creates.set(id, spec); items.update(data); },
    });
  }

  // --- unsaved state: a dashed ring on the box, the saved values in its card and title ----
  // An edit that restores the saved value counts as none. payload.segments holds the saved
  // slots: edits re-render from copies (reseg), and only a save replaces them.
  const orgInitials = Object.fromEntries(payload.orgs.map((o) => [o.id, o.initials]));
  const initialsOf = (ids) => ids.map((id) => orgInitials[id] ?? "?").join(", ") || "nikdo";
  const sameIds = (a, b) => [...a].sort().join() === [...b].sort().join();
  const nameText = (name) => name ? `„${name}“` : "podle aktivity";
  const NEW_NOTE = "Nový slot, zatím neuložený";
  function pendingOf(it) {
    if (creates.has(String(it.id))) return { cls: "cp-new", title: NEW_NOTE, footer: NEW_NOTE };
    const id = it.slotId;
    if (!moves.has(id) && !updates.has(id)) return null;
    const saved = payload.segments.find((g) => g.slot_id === id);
    const was = {}, parts = [];   // parts: the title's one-line summary
    const { abs_start_min: s, abs_end_min: e } = saved, moved = moves.get(id);
    if (moved && (moved.start_at !== absToNaive(s) || moved.end_at !== absToNaive(e))) {
      was.when = spanLabel(s, e, rowOf(it._seg.abs_start_min));   // the day only if it changed
      parts.push(was.when);
    }
    const upd = updates.get(id) || {};
    const retyped = upd.role != null && upd.role !== saved.role;
    const renamed = upd.override_name !== undefined &&
      upd.override_name !== (saved.override_name || null);
    if (retyped) parts.push(`typ ${ROLE_LABEL[saved.role]}`);
    if (renamed) parts.push(`název ${nameText(saved.override_name)}`);
    if (retyped || renamed) was.heading = roleHeading(saved.role, saved.override_name || saved.title);
    if (upd.org_ids && !sameIds(upd.org_ids, saved.attending)) {
      was.attending = saved.attending;
      parts.push(`účastníci ${initialsOf(saved.attending)}`);
    }
    return parts.length ? { cls: "cp-changed", title: `Neuloženo, původně ${parts.join("; ")}`,
                            footer: "Obsahuje neuložené změny", was } : null;
  }
  setPending(pendingOf);
  // the hover title, set as the pointer arrives so it is current (vis re-renders boxes)
  container.addEventListener("mouseover", (e) => {
    if (!editing) return;
    const box = e.target.closest(".vis-item"), id = timeline.getEventProperties(e).item;
    if (!box || id == null) return;
    const title = pendingOf(items.get(id))?.title;
    if (title) box.title = title; else box.removeAttribute("title");
  });

  // --- delete (action bar on the selected slot) ------------------------------
  function deleteSelected() {
    const [id] = timeline.getSelection();
    if (id == null) return;
    const snap = items.get(id);
    if (!snap?._seg) return;
    const key = String(id);
    if (creates.has(key)) {
      const spec = creates.get(key);
      creates.delete(key); items.remove(id);
      record({
        of: "new:" + key,
        label: `Smazán nový slot „${nameOf(snap)}“: ${spanLabel(snap._seg.abs_start_min, snap._seg.abs_end_min)}`,
        undo: () => { creates.set(key, spec); items.add(snap); },
        redo: () => { creates.delete(key); items.remove(id); },
      });
    } else if (snap.slotId != null) {
      const slotId = snap.slotId;
      const segs = segsOf(slotId);   // every row of a multi-row slot
      const ids = segs.map((it) => it.id);
      // named as saved: its pending edits leave the list (see gone)
      const saved = payload.segments.find((g) => g.slot_id === slotId);
      deletes.add(slotId); items.remove(ids);
      record({
        of: "slot:" + slotId, deleting: true,
        label: `Smazán slot „${nameOf({ _seg: saved })}“: ${spanLabel(saved.abs_start_min, saved.abs_end_min)}`,
        undo: () => { deletes.delete(slotId); items.add(segs); },
        redo: () => { deletes.add(slotId); items.remove(ids); },
      });
    }
  }

  // --- duplicate (a pending create on the same spot, to drag elsewhere) --------
  // A cut piece (multi-row or clamped) is not offered: it is not the whole slot.
  function duplicate(it) {
    const src = it._seg;
    const id = "new-" + (++tempSeq);
    const seg = { ...src, idx: id, slot_id: null };
    const spec = { activity_id: src.activity_id, role: src.role, ...itemTimes(it),
                   org_ids: src.attending, override_name: src.override_name || null };
    const data = { id, group: it.group, start: it.start, end: it.end, slotId: null,
                   _seg: seg, ...segData(seg) };
    const add = () => { creates.set(id, spec); items.add(data); };
    add();
    selectItem(id);   // the bar and card move to the copy, uncovering the original
    record({
      of: "new:" + id,
      label: `Duplikován slot „${nameOf(it)}“: ${spanLabel(src.abs_start_min, src.abs_end_min)}`,
      undo: () => { creates.delete(id); items.remove(id); },
      redo: add,
    });
  }

  // Re-render items from a patched copy of their _seg: an unedited slot's _seg is its saved
  // segment.
  function reseg(itemList, patch) {
    itemList.forEach((it) => {
      if (!it._seg) return;
      const seg = { ...it._seg, ...patch };
      items.update({ id: it.id, _seg: seg, ...segData(seg) });
    });
  }
  const piecesOf = (item) => creates.has(String(item.id)) ? [items.get(item.id)] : segsOf(item.slotId);

  // --- change slot type (role) ----------------------------------------------
  function changeSlotType(item, newRole) {
    const before = item.role || "main", slotId = item.slotId;
    if (newRole === before) return;
    const spec = creates.get(String(item.id)), prev = updates.get(slotId);
    const set = (role, net) => {   // net: the slot's batch entry, undefined for none
      reseg(piecesOf(item), { role });
      if (spec) spec.role = role;
      else if (net) updates.set(slotId, net); else updates.delete(slotId);
    };
    const next = { ...prev, role: newRole };
    set(newRole, next);
    record({
      of: slotKey(item),
      fold: { key: "role:" + slotKey(item), from: before, to: newRole, same: (a, b) => a === b,
              label: (a, b) => `Změněn typ „${nameOf(item)}“: ${ROLE_LABEL[a]} → ${ROLE_LABEL[b]}` },
      undo: () => set(before, prev), redo: () => set(newRole, next),
    });
  }

  // role-picker modal (chips, current role pre-selected); used from the edit-mode action bar
  function openSlotType(item) {
    const roles = chipGroup(Object.entries(ROLE_LABEL), { selected: item.role || "main" });
    formModal({
      title: "Typ slotu",
      okLabel: "Použít",   // into the batch, saved by Uložit
      pane: el("div", { class: "cp-pane" }, roles.node),
      onSubmit: (close) => { close(); changeSlotType(item, roles.get()); },
    });
  }

  // --- floating action bar (timeline.js): this mode's actions -----------------------
  setBarActions((it) => {
    if (!it._seg) return [];   // the new-slot placeholder while its picker is open
    return editing ? [
      { label: "✎ Upravit slot", key: "u", main: true, onClick: () => editSlot(it) },
      !it._seg.cont_back && !it._seg.cont_fwd && { label: "⧉ Duplikovat", key: "d", onClick: () => duplicate(it) },
      { label: "↺ Typ slotu", key: "t", onClick: () => openSlotType(it) },
      { label: "🗑 Smazat blok", danger: true, onClick: deleteSelected },
    ] : [
      it.slotId != null && { label: "Přiřadit orgy", key: "o", onClick: () => assignOrgs(it) },
      { label: "ℹ️ Detail", key: "d", main: true, onClick: () => openDetail(it) },
    ];
  });

  // --- slot attendees and name override --------------------------------------
  // The shared dialog (cpSlotOrgsEdit) of the activity detail page: outside edit mode a
  // standalone PATCH of the attendees, in edit mode attendees and name join the batch.
  function editSlot(item) {
    const seg = item._seg;
    window.cpSlotOrgsEdit({
      orgs: payload.orgs, selected: seg.attending,
      withName: true, name: seg.override_name || "", namePlaceholder: seg.title,
      onApply: (ids, name) => changeDetails(item, ids, (name || "").trim() || null),
    });
  }
  function changeDetails(item, orgIds, name) {
    const key = String(item.id), slotId = item.slotId, cur = item._seg;
    const before = { org_ids: cur.attending, override_name: cur.override_name || null };
    const after = { org_ids: orgIds, override_name: name };
    const sameOrgs = (a, b) => sameIds(a.org_ids, b.org_ids), sameName = (a, b) => a.override_name === b.override_name;
    const same = (a, b) => sameOrgs(a, b) && sameName(a, b);
    if (same(before, after)) return;
    const spec = creates.get(key), prev = updates.get(slotId);
    const set = (v, net) => {   // net: the slot's batch entry, undefined for none
      reseg(piecesOf(item), { attending: v.org_ids, override_name: v.override_name });
      if (spec) Object.assign(spec, v);
      else if (net) updates.set(slotId, net); else updates.delete(slotId);
    };
    const next = { ...prev, ...after };
    set(after, next);
    record({
      of: slotKey(item),
      fold: { key: "details:" + slotKey(item), from: before, to: after, same,
              label: (a, b) => `Upraven slot „${nameOf(item)}“: ` + [
                !sameOrgs(a, b) && `účastníci ${initialsOf(a.org_ids)} → ${initialsOf(b.org_ids)}`,
                !sameName(a, b) && `název ${nameText(a.override_name)} → ${nameText(b.override_name)}`,
              ].filter(Boolean).join("; ") },
      undo: () => set(before, prev), redo: () => set(after, next),
    });
  }
  function assignOrgs(item) {
    const slotId = item.slotId;
    window.cpSlotOrgsEdit({
      orgs: payload.orgs,
      selected: item._seg?.attending || [],
      url: withId(EDIT.slot, slotId),
      onSaved: (_orgs, ids) => {
        // committed, so the saved state follows
        payload.segments.forEach((g) => { if (g.slot_id === slotId) g.attending = ids; });
        reseg(segsOf(slotId), { attending: ids });
        applyHeights();   // attendees changed → refresh the display filter's dim (e.g. an "attending:" filter)
        showBar();        // and the card
      },
    });
  }

  // --- save (one PATCH, then refresh the authoritative state in place) -------
  // force=true makes the server skip the optimistic-lock check and overwrite
  // whatever is there (used from the conflict dialog's "Přepsat").
  async function save(force) {
    if (!hasPending()) return;
    saveBtn.disabled = true;
    const kept = (map) => [...map].filter(([slotId]) => !deletes.has(slotId));   // a deleted slot's edits go unsent
    const body = {
      rev: camp.rev,
      force: !!force,
      moves: kept(moves).map(([slot_id, t]) => ({ slot_id, ...t })),
      creates: [...creates.values()],
      updates: kept(updates).map(([slot_id, v]) => ({ slot_id, ...v })),
      deletes: [...deletes],
    };
    try {
      await api("PATCH", EDIT.save, body);        // CSRF refresh-and-retry handled inside
    } catch (e) {
      if (e.status === 409) { openConflict(); return; }
      toast(e.message, true);
      saveBtn.disabled = false;
      return;
    }
    // PATCH committed. Refresh in place; if anything about that fails, reload for the
    // authoritative state rather than leave committed changes shown as pending.
    try {
      const fresh = await api("GET", EDIT.save);  // same URL, GET = the re-sliced timeline
      moves.clear(); updates.clear(); deletes.clear(); creates.clear();
      history.length = 0; redoStack.length = 0;
      // fresh items get new ids: a saved slot stays selected by its slot id (a new one has none yet)
      const keptSlot = items.get(timeline.getSelection()[0] ?? -1)?.slotId;
      rehydrate(fresh);      // rebuild the items + fresh rev, no page reload
      recomputeSegCount();
      if (keptSlot != null) selectItem(segsOf(keptSlot)[0]?.id ?? null);
      setEditing(false);     // leave edit mode, as the old reload did
      toast("Časový plán uložen");
    } catch (e) {
      toastNext("Časový plán uložen");            // survives the reload
      reload();
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
    const force = el("button", { type: "button", class: "cp-primary" }, "Přepsat (force)");
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

  // --- edit-mode toggle ------------------------------------------------------
  function setEditing(on) {
    editing = on;
    frame.classList.toggle("cp-editing", on);   // the CSS hangs every edit-mode look off it
    toggleBtn.innerHTML = on ? "Zrušit" : toggleLabel;
    for (const b of [saveBtn, changesBtn]) if (b) b.hidden = !on;
    if (!on) closeChanges();
    // No per-item `editable`: that overrides itemsAlwaysDraggable and would force a
    // select-first step. The global editable + itemsAlwaysDraggable make every box
    // drag/resize directly; multi-segment slots are guarded in onMove. A touch screen keeps
    // vis's select-first step (a long press selects), so a swipe over a box pans instead.
    // Only attach the callbacks when enabling: vis rejects `undefined` for them, and with
    // editable:false they never fire anyway, so there's no need to clear them on exit.
    const opts = {
      editable: on ? { add: true, updateTime: true, updateGroup: true, remove: false, overrideItems: false } : false,
      itemsAlwaysDraggable: on && canHover()
        ? { item: true, range: true } : { item: false, range: false },
    };
    if (on) { opts.onMove = onMove; opts.onMoving = onMoving; opts.onAdd = onAdd; }
    timeline.setOptions(opts);
    // vis bakes editability into each item's DOM at render time, so re-add the program
    // items to make them pick up the new editable/itemsAlwaysDraggable options (per the mock).
    const ids = items.getIds({ filter: (it) => it._base != null });
    const data = items.get(ids), [sel] = timeline.getSelection();
    items.remove(ids);
    items.add(data);
    if (sel != null && items.get(sel)) selectItem(sel);   // the removal dropped it; keys go on from it
    applyHeights();   // re-bake the fades
    refresh();
  }

  // Revert every change in place (each is invertible) and leave edit mode (no reload).
  function discardChanges() {
    while (history.length) history.pop().undo();
    redoStack.length = 0;
    applyHeights();
    setEditing(false);
  }
  toggleBtn.addEventListener("click", () => {
    if (!editing) setEditing(true);
    else if (!hasPending()) discardChanges();   // undoes changes that net out to nothing
    else openChangesConfirm({
      question: "Zahodit tyto změny?", confirmLabel: "Zahodit", danger: true,
      onConfirm: discardChanges,
    });
  });
  if (saveBtn) saveBtn.addEventListener("click", () => {
    if (hasPending()) openChangesConfirm({ question: "Uložit tyto změny?", confirmLabel: "Uložit", onConfirm: save });
  });
  if (undoBtn) undoBtn.addEventListener("click", undo);
  if (redoBtn) redoBtn.addEventListener("click", redo);
  if (changesBtn) changesBtn.addEventListener("click", toggleChanges);
  // The editor's keys (the page's: timeline.js). Shift+E toggles edit mode: Shift because it
  // works with nothing selected, where a stray E is likeliest. Ctrl/Cmd+Z, Y, S undo, redo and
  // save (not the browser's "save page"). On the selected slot Shift+arrows step it a snap or a
  // day, Ctrl/Cmd+Shift+←/→ move its end, Delete removes it, N adds one right after it. A step
  // goes the drag's way, so it undoes and folds like one. Not Alt+Shift: on Windows that
  // switches the keyboard layout.
  const STEPS = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
  // `moved`: the piece to change, with its new times; `row` keeps a re-sliced slot's selection
  function keyEdit(it, moved, row) {
    if (isLocked(it)) {
      const change = lockedSlotEdit(moved);
      if (!change) return;
      change.redo();
      const pieces = segsOf(it.slotId);
      selectItem((pieces.find((p) => p.group === row) || pieces[0]).id);
      record(change);
    } else if (payload.groups[moved.group] && relMin(moved.start) >= 0 && relMin(moved.end) <= DAY_MIN
               && moved.end - moved.start >= camp.snap_minutes * 60000) {   // stays in its row, as a drag does
      onMove(moved, (x) => { if (x) items.update({ id: x.id, group: x.group, start: x.start, end: x.end }); });
    }
  }
  // the piece holding a slot's end (a multi-row slot's last row)
  const lastPiece = (it) => isLocked(it) ? segsOf(it.slotId).find((p) => !p._seg.cont_fwd) : it;
  document.addEventListener("keydown", (e) => {
    if (busy(e) || e.altKey) return;
    const mod = e.ctrlKey || e.metaKey, k = e.key.toLowerCase();
    if (k === "e" && e.shiftKey && !mod) { e.preventDefault(); if (!e.repeat) toggleBtn.click(); return; }
    if (!editing) return;
    if (mod && !STEPS[e.key]) {
      if (k === "z" && !e.shiftKey) { e.preventDefault(); undo(); }
      else if (k === "y" || (k === "z" && e.shiftKey)) { e.preventDefault(); redo(); }
      else if (k === "s" && !e.shiftKey) { e.preventDefault(); if (!e.repeat) saveBtn?.click(); }
      return;
    }
    const [id] = timeline.getSelection();
    const it = id != null && items.get(id);
    if (!it?._seg) return;
    if ((e.key === "Delete" || e.key === "Backspace") && !mod && !e.shiftKey) {
      e.preventDefault(); deleteSelected(); return;
    }
    if (k === "n" && !mod && !e.shiftKey) {   // up to an hour, from its end
      e.preventDefault();
      if (e.repeat) return;
      const last = lastPiece(it), s = relMin(last.end), end = Math.min(s + 60, DAY_MIN);
      if (end - s < camp.snap_minutes) toast("Za slotem už v tomto dni není místo.", true);
      else addSlot(Number(last.group), s, end, id);
      return;
    }
    const step = e.shiftKey && STEPS[e.key];
    if (!step) return;
    e.preventDefault();
    const ms = step[0] * camp.snap_minutes * 60000;
    if (mod) {   // the end, on the piece that holds it
      const last = lastPiece(it);
      if (ms && last) keyEdit(it, { ...last, end: new Date(+last.end + ms) }, Number(it.group));
      return;
    }
    const row = Number(it.group) + step[1];
    keyEdit(it, { ...it, group: row, start: new Date(+it.start + ms), end: new Date(+it.end + ms) }, row);
  });
  window.addEventListener("beforeunload", (e) => {
    if (!reloading && hasPending()) { e.preventDefault(); e.returnValue = ""; }
  });
};
