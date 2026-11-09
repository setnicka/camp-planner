// Camp Planner: timeline editor (loaded only when the user can edit).
// timeline.js calls window.cpTimelineEdit(ctx) with the render context it shares.
"use strict";

window.cpTimelineEdit = function setupEditing(ctx) {
  const { EDIT, payload, camp, container, items, timeline, DAY_MIN, WINDOW_START, winStart, Y, Mo, D, ROLE_LABEL, roleHeading, fmtClock, dayName, mToDate, applyHeights, segmentContent, buildItem, orgById, rehydrate, selectItem, selectItems, selectedItems, openDetail, busy, escapeHtml, setBarActions, setPending, setMultiCard, showBar, hideBar } = ctx;
  const { el, api, withId, canHover, stampTheme, openModal, formModal, chipGroup, toast, toastNext, plural } = window.cpDom;
  const pad = (n) => String(n).padStart(2, "0");
  const catById = Object.fromEntries(payload.categories.map((c) => [c.id, c]));

  let editing = false;
  let tempSeq = 0;
  let reloading = false;  // set before our own location.reload() so beforeunload stays quiet
  let saving = false;     // a save in flight: editing waits, as its success clears the batch
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
  // a selection's slots, each once: a multi-row slot's pieces are one
  function uniqueSlots(list) {
    const seen = new Set();
    return list.filter((it) => it._seg && !seen.has(slotKey(it)) && seen.add(slotKey(it)));
  }
  const moveFold = (key, title, from, to) => ({
    key: "move:" + key, from, to,
    same: (a, b) => a[0] === b[0] && a[1] === b[1],
    label: (a, b) => `${b[1] - b[0] === a[1] - a[0] ? "Přesunut" : "Změněna velikost"} „${title}“: ${moveLabel(a, b)}`,
  });
  // A new slot's one row in the change list, read off the slot as it is now: its later
  // edits fold into it.
  const newSlotLabel = (verb, id) => () => {
    const it = items.get(id), seg = it._seg;
    return `${verb} slot „${nameOf(it)}“: ${spanLabel(seg.abs_start_min, seg.abs_end_min)}`
      + (seg.role !== "main" ? `, ${ROLE_LABEL[seg.role]}` : "")
      + (seg.attending.length ? `, účastníci ${initialsOf(seg.attending)}` : "");
  };
  const hasPending = () => foldedChangeLines().length > 0;
  const OUT_OF_CAMP = "Mimo rozsah tábora.";

  // record() only logs (the caller has already applied the forward effect); undo()/redo()
  // re-apply the before/after state to both the items DataSet and the net batch maps.
  function record(change) { history.push(change); redoStack.length = 0; afterChange(); }
  // The changes one action made to several slots: undone as one, listed each on its own.
  function recordAll(changes) {
    if (changes.length === 1) return record(changes[0]);
    if (!changes.length) return;
    record({
      parts: changes,
      undo: () => changes.slice().reverse().forEach((c) => c.undo()),
      redo: () => changes.forEach((c) => c.redo()),
    });
  }
  function undo() { const c = history.pop(); if (c) { c.undo(); redoStack.push(c); afterChange(); } }
  function redo() { const c = redoStack.pop(); if (c) { c.redo(); history.push(c); afterChange(); } }
  function afterChange() { showBar(); applyHeights(); refresh(); }   // the bar follows its slot

  function pluralChanges(n) {
    return `${n} ${plural(n, "změna", "změny", "změn")}`;
  }
  function refresh() {
    const lines = foldedChangeLines(), n = lines.length;
    if (saveBtn) saveBtn.disabled = saving || !n;
    if (undoBtn) undoBtn.disabled = saving || !history.length;
    if (redoBtn) redoBtn.disabled = saving || !redoStack.length;
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
    for (const c of history.flatMap((h) => h.parts || [h])) {
      if (gone(c) || (c.of.startsWith("new:") && !c.creating)) continue;
      if (!c.fold) { rows.push(c.creating ? c.label() : c.label); continue; }
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
    stampTheme(changesPanel);   // when shown, as timeline.js's bar
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
  // A drop of several selected boxes calls onMove once per box, all in one go: they gather
  // here and move as one change, led by the box under the pointer.
  let dropped = null;   // [{ item, callback }] of the drop under way
  let grabbed = null;   // id of the box the pointer went down on
  container.addEventListener("pointerdown", (e) => { grabbed = timeline.getEventProperties(e).item; });
  function onMove(item, callback) {
    dropGhost();
    if (saving) { callback(null); return; }
    if (!dropped) { dropped = []; queueMicrotask(applyDrop); }
    dropped.push({ item, callback });
  }
  function applyDrop() {
    const list = dropped;
    dropped = null;
    showBar();   // back after the drag hid it, also when nothing changed; it waits a frame for vis
    if (list.length === 1) { moveBox(list[0].item, list[0].callback); return; }
    list.forEach(({ callback }) => callback(null));   // back in place, moveSlots moves all or none
    const lead = (list.find(({ item }) => item.id === grabbed) || list[0]).item, cur = items.get(lead.id);
    moveSlots(list.map(({ item }) => items.get(item.id)), Number(lead.group) - Number(cur.group),
      lead.start - cur.start);
  }
  // vis applies the visual move via callback(item); we sync the batch map + log a change.
  function moveBox(item, callback) {
    if (isLocked(item)) {  // multi-row (window-crossing) slot: re-slice the whole slot from this one drag
      const change = lockedSlotEdit(item);
      callback(null);                        // always cancel vis's single-piece move; we re-render instead
      if (change) applyLocked(change, item.slotId, Number(item.group));
      else if (change === false) toast(OUT_OF_CAMP, true);
      return;
    }
    const change = boxChange(item);
    callback(item);                              // apply the visual move first…
    if (change) { change.redo(); record(change); }   // …refresh box, card data and batch, then log
  }
  // The change putting a one-row box where `item` has it, null when it stays: redo() applies it.
  function boxChange(item) {
    const id = item.id, key = String(id);
    const cur = items.get(id) || {};
    const seg = cur._seg;
    const title = nameOf(cur);
    const before = snapshot(id);
    const after = { start: item.start, end: item.end, group: item.group };
    if (before && +before.start === +after.start && +before.end === +after.end &&
        String(before.group) === String(after.group)) {
      return null;  // dropped back where it started → not a change
    }
    const afterTimes = itemTimes(item);
    // .ev-time and the card read the segment's abs range
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

    if (creates.has(key)) {
      const spec = creates.get(key);
      const beforeTimes = { start_at: spec.start_at, end_at: spec.end_at };
      return {
        of, fold,
        undo: () => { items.update({ id, ...before, ...beforeRender }); Object.assign(spec, beforeTimes); },
        redo: () => { items.update({ id, ...after, ...afterRender }); Object.assign(spec, afterTimes); },
      };
    }
    if (item.slotId == null) return null;   // the new-slot placeholder
    const slotId = item.slotId;
    const prev = moves.has(slotId) ? moves.get(slotId) : null;
    return {
      of, fold,
      undo: () => { items.update({ id, ...before, ...beforeRender }); if (prev) moves.set(slotId, prev); else moves.delete(slotId); },
      redo: () => { items.update({ id, ...after, ...afterRender }); moves.set(slotId, afterTimes); },
    };
  }

  // Shifts the slots of `pieces` by `rows` days and `ms`, all or none, as one change. Each box
  // stays within a day row, as a key step keeps it; a multi-row slot moves whole, re-sliced.
  function moveSlots(pieces, rows, ms) {
    if (!rows && !ms) return;
    const targets = [];
    for (const it of uniqueSlots(pieces)) {
      const moved = { ...it, group: Number(it.group) + rows,
                      start: new Date(+it.start + ms), end: new Date(+it.end + ms) };
      if (!isLocked(it) && !(payload.groups[moved.group] && relMin(moved.start) >= 0
                             && relMin(moved.end) <= DAY_MIN)) {
        toast(OUT_OF_CAMP, true);
        return;
      }
      targets.push(moved);
    }
    const keep = keepSelection(rows);
    const changes = [];
    for (const moved of targets) {   // each builds on the slot as it is, so all before any applies
      const change = isLocked(moved) ? lockedSlotEdit(moved) : boxChange(moved);
      if (change === false) { toast(OUT_OF_CAMP, true); return; }
      if (change) changes.push(change);
    }
    changes.forEach((c) => c.redo());
    keep();
    recordAll(changes);
  }
  // The selection as slots, to restore once their pieces are re-sliced (new ids): each the
  // piece in its row moved by `rows`, else its first.
  function keepSelection(rows) {
    const was = selectedItems().filter((it) => it._seg)
      .map((it) => ({ key: slotKey(it), row: Number(it.group) + rows }));
    return () => selectItems([...new Set(was.map(({ key, row }) => {
      const pieces = items.get({ filter: (it) => it._seg && slotKey(it) === key });
      return (pieces.find((p) => Number(p.group) === row) || pieces[0])?.id;
    }).filter((id) => id != null))]);
  }

  // live time-in-box while dragging/resizing: rewrite the .ev-time text directly
  // (mutating item.content would kill the drag). Locked (multi-row) slots get the same
  // live feedback; they re-slice to the final layout on drop.
  const cssId = (id) => (window.CSS && CSS.escape) ? CSS.escape(String(id)) : String(id);
  // Per box being dragged (several when a selection moves): its time label, cached across the
  // drag's many onMoving frames, and a hatched copy that stays where the drag started until the
  // drop. A plain node in the box's row, not a vis item, so it can't push the other boxes into
  // new lanes.
  const dragged = new Map();   // item id -> { time, ghost }
  function dropGhost() { dragged.forEach((d) => d.ghost?.remove()); dragged.clear(); }
  timeline.on("rangechange", dropGhost);   // a pan or zoom mid-drag would leave it misplaced
  // the browser taking a drag over (a long-press menu, a scroll) ends it without onMove
  container.addEventListener("pointercancel", () => { if (dragged.size) { dropGhost(); showBar(); } });
  function ghostOf(box) {
    // held from the row's bottom, as vis lays its lanes, so it stays put while the row restacks
    const { transform, width } = box.style;
    const bottom = box.parentNode.offsetHeight - box.offsetTop - box.offsetHeight;
    const ghost = el("div", { class: "vis-item cp-ghost" });
    Object.assign(ghost.style, { transform, width, top: "auto", bottom: bottom + "px",
      height: box.offsetHeight + "px" });
    box.before(ghost);
    return ghost;
  }
  function onMoving(item, callback) {
    if (saving) { callback(null); return; }
    let d = dragged.get(item.id);
    if (!d?.time?.isConnected) {
      const time = container.querySelector('.ev-time[data-id="' + cssId(item.id) + '"]');
      const box = time?.closest(".vis-item");
      // first frame, before the callback: the box is still where it started
      d = { time, ghost: d ? d.ghost : box && ghostOf(box) };
      dragged.set(item.id, d);
    }
    callback(item);
    if (d.time) {
      d.time.textContent = `${fmtClock(absMinOf(item.group, item.start))}–${fmtClock(absMinOf(item.group, item.end))}`;
    }
    hideBar();   // the bar and card stay behind as the box moves; back on drop
  }

  // --- band selection (Shift/Ctrl+drag over empty space, mouse only) ---------
  // Selects the slots the band touches, across days; Ctrl adds them to the selection. Taken
  // in the capture phase, before vis would pan.
  container.addEventListener("pointerdown", (e) => {
    const add = e.ctrlKey || e.metaKey;
    if (!editing || saving || e.pointerType !== "mouse" || e.button !== 0 || !(add || e.shiftKey)
        || !e.target.closest(".vis-center") || e.target.closest(".vis-item")) return;
    e.preventDefault(); e.stopPropagation();
    const x0 = e.clientX, y0 = e.clientY;
    const band = el("div", { class: "cp-tl-band" });
    let rect = null;
    const move = (m) => {
      rect = { left: Math.min(x0, m.clientX), right: Math.max(x0, m.clientX),
               top: Math.min(y0, m.clientY), bottom: Math.max(y0, m.clientY) };
      Object.assign(band.style, { left: rect.left + "px", top: rect.top + "px",
        width: rect.right - rect.left + "px", height: rect.bottom - rect.top + "px" });
    };
    container.addEventListener("pointermove", move);
    container.addEventListener("lostpointercapture", () => {   // released or cancelled
      container.removeEventListener("pointermove", move);
      band.remove();
      if (!rect) return;
      const hit = new Set([...container.querySelectorAll(".ev-time[data-id]")].filter((t) => {
        const r = t.closest(".vis-item").getBoundingClientRect();
        return r.left < rect.right && r.right > rect.left && r.top < rect.bottom && r.bottom > rect.top;
      }).map((t) => t.dataset.id));
      const ids = items.getIds({ filter: (it) => hit.has(String(it.id)) });
      selectItems(add ? [...new Set([...timeline.getSelection(), ...ids])] : ids);
    }, { once: true });
    container.setPointerCapture(e.pointerId);
    document.body.append(band);
    stampTheme(band);
  }, true);

  // --- add (double-tap empty space) ------------------------------------------
  // vis also adds on a Ctrl+drag, here a Ctrl+click on a slot that slipped: not an add
  let ctrlDown = false;
  container.addEventListener("pointerdown", (e) => { ctrlDown = e.ctrlKey || e.metaKey; });
  function onAdd(item, callback) {
    callback(null); // we manage our own placeholder item instead of vis's default
    if (saving || ctrlDown) return;
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
    selectItem(null);
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
    seg.idx = "rs" + (++tempSeq);
    return buildItem(seg);
  }

  // Map a drag/resize of ONE segment onto its owning slot, then re-slice. Returns a
  // change to record, null for a no-op, false for an edit out of the camp.
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

    if (s < WINDOW_START || e > WINDOW_START + payload.groups.length * DAY_MIN) return false;
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

  // A re-sliced slot's pieces have new ids: the selection goes on from its piece in `row`.
  function applyLocked(change, slotId, row) {
    change.redo();
    const pieces = segsOf(slotId);
    selectItem((pieces.find((p) => Number(p.group) === row) || pieces[0]).id);
    record(change);
  }

  function bindNewSlot(id, group, sRel, eRel, activity, role) {
    const catKey = catById[activity.category_id]?.key ?? "_none";
    const sAbs = group * DAY_MIN + WINDOW_START + sRel, eAbs = group * DAY_MIN + WINDOW_START + eRel;
    const spec = { activity_id: activity.id, role, start_at: absToNaive(sAbs), end_at: absToNaive(eAbs) };
    // a pending create is a real segment with no slot_id yet, rendered like any other.
    const seg = {
      idx: id, title: activity.title, role, cat_key: catKey, activity_id: activity.id, slot_id: null,
      abs_start_min: sAbs, abs_end_min: eAbs, day: group, rel_start_min: sRel, rel_end_min: eRel,
      garants: [], helpers: [], attending: [], tag_ids: [], cont_back: false, cont_fwd: false,
    };
    const data = buildItem(seg);
    items.update(data);          // convert the placeholder into the real (pending) slot
    creates.set(id, spec);
    record({
      of: "new:" + id, creating: true,
      label: newSlotLabel("Vytvořen", id),
      undo: () => { creates.delete(id); items.remove(id); },
      redo: () => { creates.set(id, spec); items.update(data); },
    });
  }

  // --- unsaved state: a dashed ring on the box, the saved values in its card and title ----
  // An edit that restores the saved value counts as none. payload.segments holds the saved
  // slots: edits re-render from copies (reseg), and only a save replaces them.
  const initialsOf = (ids) => ids.map((id) => orgById[id]?.initials ?? "?").join(", ") || "nikdo";
  const sameIds = (a, b) => [...a].sort().join() === [...b].sort().join();
  const nameText = (name) => name ? `„${name}“` : "podle aktivity";
  const NEW_NOTE = "Nový slot, zatím neuložený";
  const savedOf = (id) => payload.segments.find((g) => g.slot_id === id);
  const isMoved = (id) => {
    const moved = moves.get(id), saved = savedOf(id);
    return !!moved && (moved.start_at !== absToNaive(saved.abs_start_min)
      || moved.end_at !== absToNaive(saved.abs_end_min));
  };
  // Only the fields that differ from the saved slot: one sent unchanged would overwrite a
  // concurrent edit of it, as the slot PATCH (attendees outside edit mode) keeps the rev.
  function changedFields(id) {
    const upd = updates.get(id) || {}, saved = savedOf(id), out = {};
    if (upd.role != null && upd.role !== saved.role) out.role = upd.role;
    if (upd.override_name !== undefined && upd.override_name !== (saved.override_name || null)) {
      out.override_name = upd.override_name;
    }
    if (upd.org_ids && !sameIds(upd.org_ids, saved.attending)) out.org_ids = upd.org_ids;
    return out;
  }
  function pendingOf(it) {
    if (creates.has(String(it.id))) return { cls: "cp-new", title: NEW_NOTE, footer: NEW_NOTE };
    const id = it.slotId;
    if (!moves.has(id) && !updates.has(id)) return null;
    const saved = savedOf(id);
    const was = {}, parts = [];   // parts: the title's one-line summary
    if (isMoved(id)) {   // the day only if it changed
      was.when = spanLabel(saved.abs_start_min, saved.abs_end_min, rowOf(it._seg.abs_start_min));
      parts.push(was.when);
    }
    const diff = changedFields(id);
    const retyped = "role" in diff, renamed = "override_name" in diff;
    if (retyped) parts.push(`typ ${ROLE_LABEL[saved.role]}`);
    if (renamed) parts.push(`název ${nameText(saved.override_name)}`);
    if (retyped || renamed) was.heading = roleHeading(saved.role, saved.override_name || saved.title);
    if (diff.org_ids) {
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

  // --- delete (action bar on the selected slots) -----------------------------
  // A multi-row slot goes once, with every row.
  function deleteSlots(list) {
    const changes = uniqueSlots(list).map(deleteChange).filter(Boolean);
    changes.forEach((c) => c.redo());
    recordAll(changes);
  }
  function deleteChange(snap) {
    const id = snap.id, key = String(id);
    if (creates.has(key)) {
      const spec = creates.get(key);
      return {
        of: "new:" + key,
        label: `Smazán nový slot „${nameOf(snap)}“: ${spanLabel(snap._seg.abs_start_min, snap._seg.abs_end_min)}`,
        undo: () => { creates.set(key, spec); items.add(snap); },
        redo: () => { creates.delete(key); items.remove(id); },
      };
    }
    if (snap.slotId == null) return null;
    const slotId = snap.slotId;
    const segs = segsOf(slotId);   // every row of a multi-row slot
    const ids = segs.map((it) => it.id);
    // named as saved: its pending edits leave the list (see gone)
    const saved = savedOf(slotId);
    return {
      of: "slot:" + slotId, deleting: true,
      label: `Smazán slot „${nameOf({ _seg: saved })}“: ${spanLabel(saved.abs_start_min, saved.abs_end_min)}`,
      undo: () => { deletes.delete(slotId); items.add(segs); },
      redo: () => { deletes.add(slotId); items.remove(ids); },
    };
  }

  // --- duplicate (a pending create on the same spot, to drag elsewhere) --------
  // A cut piece (multi-row or clamped) is not offered: it is not the whole slot.
  function duplicate(it) {
    const src = it._seg;
    const id = "new-" + (++tempSeq);
    const seg = { ...src, idx: id, slot_id: null };
    const spec = { activity_id: src.activity_id, role: src.role, ...itemTimes(it),
                   org_ids: src.attending, override_name: src.override_name || null };
    const data = buildItem(seg);
    const add = () => { creates.set(id, spec); items.add(data); };
    add();
    selectItem(id);   // the bar and card move to the copy, uncovering the original
    record({
      of: "new:" + id, creating: true,
      label: newSlotLabel("Duplikován", id),
      undo: () => { creates.delete(id); items.remove(id); },
      redo: add,
    });
  }

  // Re-render items from a patched copy of their _seg: an unedited slot's _seg is its saved
  // segment.
  function reseg(itemList, patch) {
    itemList.forEach((it) => {
      if (!it._seg) return;
      items.update(buildItem({ ...it._seg, ...patch }));
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
  const pluralSlots = (n) => `${n} ${plural(n, "slot", "sloty", "slotů")}`;
  setBarActions((it, sel) => {
    if (!it._seg || saving) return [];   // the new-slot placeholder while its picker is open
    const slots = uniqueSlots(sel);
    if (slots.length > 1) return [
      { label: `🗑 Smazat ${pluralSlots(slots.length)}`, danger: true, onClick: () => deleteSlots(slots) },
    ];
    return editing ? [
      { label: "✎ Upravit slot", key: "u", main: true, onClick: () => editSlot(it) },
      !it._seg.cont_back && !it._seg.cont_fwd && { label: "⧉ Duplikovat", key: "d", onClick: () => duplicate(it) },
      { label: "↺ Typ slotu", key: "t", onClick: () => openSlotType(it) },
      { label: "🗑 Smazat blok", danger: true, onClick: () => deleteSlots([it]) },
    ] : [
      it.slotId != null && { label: "Přiřadit orgy", key: "o", onClick: () => assignOrgs(it) },
      { label: "ℹ️ Detail", key: "d", main: true, onClick: () => openDetail(it) },
    ];
  });
  // several selected slots: their names and times, in time order
  setMultiCard((sel) => {
    const slots = uniqueSlots(sel).sort((a, b) => a._seg.abs_start_min - b._seg.abs_start_min);
    if (slots.length < 2) return null;
    return [`<b>Vybrané sloty: ${slots.length}</b>`, ...slots.map((it) =>
      `${escapeHtml(roleHeading(it._seg.role, nameOf(it)))}: ${spanLabel(it._seg.abs_start_min, it._seg.abs_end_min)}`,
    )].join("<br>");
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
      selected: item._seg.attending,
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
  function setSaving(on) { saving = on; toggleBtn.disabled = on; refresh(); showBar(); }
  async function save(force) {
    if (!hasPending()) return;
    setSaving(true);
    const kept = (map) => [...map.keys()].filter((slotId) => !deletes.has(slotId));   // a deleted slot's edits go unsent
    const body = {
      rev: camp.rev,
      force: !!force,
      moves: kept(moves).filter(isMoved).map((slot_id) => ({ slot_id, ...moves.get(slot_id) })),
      creates: [...creates.values()],
      updates: kept(updates).map((slot_id) => ({ slot_id, ...changedFields(slot_id) }))
        .filter((u) => Object.keys(u).length > 1),
      deletes: [...deletes],
    };
    try {
      await api("PATCH", EDIT.save, body);        // CSRF refresh-and-retry handled inside
    } catch (e) {
      setSaving(false);
      if (e.status === 409) openConflict(); else toast(e.message, true);
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
      setSaving(false);
      setEditing(false);     // leave edit mode, as the old reload did
      toast("Časový plán uložen");
    } catch (e) {
      toastNext("Časový plán uložen");            // survives the reload
      reload();
    }
  }

  // shown when the save hit a stale-rev conflict: explain + offer the three ways out
  function openConflict() {
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
    if (!on) closeChanges();
    // No per-item `editable`: that overrides itemsAlwaysDraggable and would force a
    // select-first step. The global editable + itemsAlwaysDraggable make every box
    // drag/resize directly; multi-segment slots are guarded in onMove. A touch screen keeps
    // vis's select-first step (a long press selects), so a swipe over a box pans instead.
    // Only attach the callbacks when enabling: vis rejects `undefined` for them, and with
    // editable:false they never fire anyway, so there's no need to clear them on exit.
    // With a mouse, Ctrl/Shift+click selects several slots, a drag of one moves them all. vis
    // would also toggle a slot on a held button, which a drag often starts with: the hold it
    // takes is set to a day. A touch screen keeps one, as there the long press selects for a drag.
    const multi = on && canHover();
    const opts = {
      editable: on ? { add: true, updateTime: true, updateGroup: true, remove: false, overrideItems: false } : false,
      itemsAlwaysDraggable: multi ? { item: true, range: true } : { item: false, range: false },
      multiselect: multi, multiselectPerGroup: true,   // Shift+click spans a stretch of one day
      longSelectPressTime: multi ? DAY_MIN * 60000 : 251,   // vis's default
    };
    if (on) { opts.onMove = onMove; opts.onMoving = onMoving; opts.onAdd = onAdd; }
    timeline.setOptions(opts);
    // vis bakes editability into each item's DOM at render time, so re-add the program
    // items to make them pick up the new editable/itemsAlwaysDraggable options (per the mock).
    const ids = items.getIds({ filter: (it) => it._base != null });
    // leading last, the one a single selection keeps
    const data = items.get(ids), sel = timeline.getSelection().slice(multi ? 0 : -1);
    items.remove(ids);
    items.add(data);
    selectItems(sel.filter((id) => items.get(id)));   // the removal dropped them; keys go on from them
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
  // The editor's keys (the page's: timeline.js). Shift+E, not E: it works with nothing
  // selected, where a stray E is likeliest; not Alt+Shift, which switches the keyboard layout
  // on Windows. A step goes the drag's way, so it undoes and folds like one.
  const STEPS = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
  // `moved`: the piece to change, with its new times; `row` keeps a re-sliced slot's selection
  function keyEdit(it, moved, row) {
    if (isLocked(it)) {
      const change = lockedSlotEdit(moved);
      if (change) applyLocked(change, it.slotId, row);
      else if (change === false) toast(OUT_OF_CAMP, true);
    } else if (payload.groups[moved.group] && relMin(moved.start) >= 0 && relMin(moved.end) <= DAY_MIN
               && moved.end - moved.start >= camp.snap_minutes * 60000) {   // stays in its row, as a drag does
      moveBox(moved, (x) => { if (x) items.update({ id: x.id, group: x.group, start: x.start, end: x.end }); });
    }
  }
  // the piece holding a slot's end (a multi-row slot's last row)
  const lastPiece = (it) => isLocked(it) ? segsOf(it.slotId).find((p) => !p._seg.cont_fwd) : it;
  document.addEventListener("keydown", (e) => {
    if (busy(e) || e.altKey || saving) return;
    const mod = e.ctrlKey || e.metaKey, k = e.key.toLowerCase();
    if (k === "e" && e.shiftKey && !mod) { e.preventDefault(); if (!e.repeat) toggleBtn.click(); return; }
    if (!editing) return;
    if (mod && !STEPS[e.key]) {
      if (k === "z" && !e.shiftKey) { e.preventDefault(); undo(); }
      else if (k === "y" || (k === "z" && e.shiftKey)) { e.preventDefault(); redo(); }
      else if (k === "s" && !e.shiftKey) { e.preventDefault(); if (!e.repeat) saveBtn.click(); }
      return;
    }
    const id = timeline.getSelection().at(-1), sel = selectedItems();
    const it = id != null && items.get(id);
    if (!it?._seg) return;
    if ((e.key === "Delete" || e.key === "Backspace") && !mod && !e.shiftKey) {
      e.preventDefault(); deleteSlots(sel); return;
    }
    if (k === "n" && !mod && !e.shiftKey) {   // up to an hour, from its end
      e.preventDefault();
      if (e.repeat) return;
      const last = lastPiece(it);
      if (!last) return;   // cut off by the camp's end
      const s = relMin(last.end), end = Math.min(s + 60, DAY_MIN);
      if (end - s < camp.snap_minutes) toast("Za slotem už v tomto dni není místo.", true);
      else addSlot(Number(last.group), s, end, id);
      return;
    }
    const step = e.shiftKey && STEPS[e.key];
    if (!step) return;
    e.preventDefault();
    const ms = step[0] * camp.snap_minutes * 60000;
    if (uniqueSlots(sel).length > 1) { if (!mod) moveSlots(sel, step[1], ms); return; }
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
