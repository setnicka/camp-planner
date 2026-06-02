# Timeline mockups — vis-timeline vs GridStack

Two throwaway prototypes of the Camp Planner **main planning view**, built on the
**same real data** (`data.js`, parsed from the ŠMF Google Calendar iCal export — the full
2-week camp, So 4. 7. – Ne 19. 7. 2026; categories inferred by keyword since iCal carries no colour)
so the libraries can be compared directly. Both implement the target layout:
**one row = one day, time runs horizontally**, drag-to-move (incl. between days),
edge-resize, **overlap sub-lanes**, independent prep/cleanup slots,
and **programs that span two camp days**.

## How two-day spans work

Each event in `data.js` stores a full **start/end datetime** (DB-style, e.g. *Žraut*
`2026-07-18T18:00` → `2026-07-19T08:00`); `buildSlots()` converts those to absolute minutes
from `CAMP_START`, which are the **single source of truth**. `buildSegments()` then slices each
slot at every day-window boundary into ONE segment per row, with `contBack`/`contFwd` flags. Each renderer draws a
segment on every row the activity touches, each labelled with the activity's **true start–end**
(e.g. `Title 22:00–07:30`) plus a `«`/`»` arrow marking continuation and a dashed seam on the cut edge. Each day row is a full **24 h
window, 05:00 → 05:00 next day**, so rows tile with no gap and the whole night (incl. 03:00–05:00)
is shown. Distinguish from *crosses-midnight-within-one-day* (Banáni 22:00–02:30) — that stays a
single row because it ends before the next 05:00 boundary. The slice point is the per-camp
**day boundary** (05:00 here).

### Linked editing (both halves move together)

Every drag/resize is intercepted and mapped back onto the owning **slot** via
`applySegmentEdit()` (in `data.js`), then the view is re-sliced and re-rendered.
(An activity = a `main` slot plus optional independent `prep`/`cleanup` slots, all
sharing an `actId`; each slot is dragged/linked on its own.)

- **Move** either half of a slot → that whole slot translates (both halves follow).
- **Resize** an end → that end changes; the cut edge (row boundary) is not resizable.
- **Drag past the day's end / onto another row** → the activity's times shift, so it
  spills onto the next day automatically (and keeps moving as one).
- `applySegmentEdit()` computes from the segment's render-time snapshot and is **idempotent**
  (GridStack fires both `change` and `dropped` for one gesture — applying twice is harmless).

vis-timeline does this in its `onMove` hook (cancels its own mutation, we own the state).
GridStack keeps the activity model central and **rebuilds the per-day grids** on each edit,
listening to `change` + `dropped`; a re-entrancy guard ignores events from our own re-render.

*Verification note:* the edit math is unit-tested (move / cross-day / resize / idempotency)
and both pages load error-free, but the live **drag gesture** itself was not automatable in
the headless setup — confirm the feel in a real browser.

## Run

Just open the HTML files in a browser (they load the libraries from CDN, so you need
internet on first load):

- `mock-vis-timeline.html`
- `mock-gridstack.html`

Or serve the folder: `python3 -m http.server` then visit the files.

## Files

| File | What |
|---|---|
| `data.js` | Shared data + categories/colors + time helpers (96 × 15-min slots, full-24h 05:00→05:00 window). |
| `mock-vis-timeline.html` | vis-timeline 7.7.3 (Apache-2.0/MIT). |
| `mock-gridstack.html` | GridStack 12.6.0 (MIT). |

## What we learned (matches `../FRONTEND_RESEARCH.md`)

**vis-timeline** — *less glue, time-native.*
- Days = `groups` (rows); all days share one reference date so time-of-day aligns; the
  actual day is the group. `stack:true` does the overlap sub-lanes **for free**.
- Drag/resize via `editable`; `snap` gives 15-min snapping; midnight-crossing events
  (Banáni 22:00–02:30) and the time axis are handled by the library. `itemsAlwaysDraggable` lets
  you drag without click-selecting first. **Live in-box time during drag** is done by updating the
  `.ev-time` DOM text **directly** in `onMoving` (matched via a `data-id`).
- **Gotcha — `xss: { disabled: true }` is required.** vis sanitizes item-content HTML by default
  (DOMPurify), which **strips `class` and `data-*` attributes** from the content — so our
  `.ev-title`/`.ev-meta`/`.ev-time` styling and the `data-id` hook silently vanished (item colors
  survived only because they ride on the item's `className` option, not the content). Disabling the
  sanitizer (our content is trusted) restores styling and the live-update hook.
- **Gotcha:** don't mutate `item.content`/the DataSet in `onMoving` — that redraws the item's DOM
  and kills the in-progress drag after one step (worst for unselected items); touch the DOM directly.
- **Gotcha — kill item borders with `border: 0`, not `border: none`.** vis's `.vis-item.vis-range`
  rule (higher specificity than `.vis-item`) re-forces `border-style: solid`, so `border: none`
  leaves a `currentColor` border: invisible (white) on dark boxes but **dark-gray on the light
  lecture boxes**. `border: 0` zeroes the width so the inherited solid style draws nothing; the
  explicit `cut-l/cut-r` dashed seams set their own width and are unaffected.
- **Gotcha — gaps between boxes must be a transparent border, not `margin.item`.** vis feeds
  `margin.item.horizontal/vertical` into its stacking/collision math, so a non-zero value makes
  time-adjacent boxes look overlapping and forces spurious extra lanes. Instead keep `margin.item`
  at `0` and inset each box with `border: 2px solid transparent; background-clip: padding-box`
  (`box-sizing: border-box`, which vis sets, keeps it in its slot). **Critical:** the per-category
  colour rules must use the `background-color` **longhand** — the `background` *shorthand* resets
  `background-clip` to `border-box`, letting the fill bleed under the transparent border so the
  boxes touch again with no gap.
- Prep/cleanup are **independent slots** (own start/end, placeable anywhere — even a
  different day), faded and labelled `příprava:/úklid: <title>`, editable on their own.
- ~110 lines of JS. Felt purpose-built for a schedule.

**GridStack** — *more glue, full control.*
- One grid **per day**; 88 columns = 15-min slots (v12 needs no custom CSS); `acceptWidgets:true`
  enables drag **between** day-grids; `resizable:{handles:'e,w'}` makes resize = duration.
- We had to build ourselves: the **time-axis header**, the **time↔column math**, and the
  **overlap-lane assignment** (greedy interval packing) — GridStack's engine *prevents*
  overlap rather than laning it, so lanes are explicit `y` rows under `float:true`.
- Gotcha: v12 **escapes `content` as text** by default (XSS) — set
  `GridStack.renderCB = (el,w)=>{el.innerHTML=w.content}` to render HTML.
- Gotcha: resize handles **auto-hide until hover** and are only a few px wide, so on
  narrow (15-min) boxes they feel un-resizable. Fix: set `alwaysShowResizeHandle: true`
  and add CSS widening the `.ui-resizable-e/w` grips (we draw a visible white bar per edge).
- Gotcha: with no `maxRow` the grid **grows unbounded** as you drag a widget down (endless
  empty lanes). Fix: `maxRow: laneCount + 1` (used lanes + one spare drop row).
- **Split across the day boundary**: a small **runway** (`SPILL` cols) past 05:00, mapped linearly
  into the next day by `colToAbs()`. Drag or resize a box's edge into it; on `change` (after the
  gesture completes) `onEdit` extends the slot past 05:00 and the re-slice **splits it onto the
  next row** (grow it further there). Limited to the runway width per gesture.
- **Dead end — do NOT manipulate the grid mid-gesture.** We tried live "shrink-on-drag"
  (`grid.update({w})` inside `on('drag')`) so a moved box could be dragged arbitrarily far past
  the edge: it **broke GridStack's drag engine** — the box jumped to x=0, and the drag helper was
  orphaned (a "shadow" stuck to the cursor, other widgets reshaping after release). Lesson: only
  read node state during a gesture; commit + re-render on the completion event. Truly-unbounded
  drag-to-split isn't viable in GridStack — it's a point for **vis-timeline**, where cross-day is a
  clean vertical row-drag.
- **Live feedback**: the time label updates live during the gesture (read-only); GridStack resizes
  the box itself live during a resize.
- **Disappearing edit guard**: `applySegmentEdit()` rejects any edit that would leave a slot
  with zero visible segments (e.g. dragged past the last day) — it stays put instead of vanishing.
- ~140 lines + the lane/axis helpers. Total styling control; reconstructs time semantics.

## Takeaway

Both produce a faithful row-per-day view with overlap lanes. **vis-timeline** is the
lower-effort, time-aware fit (recommended for v1). **GridStack** is viable if we later
want pixel-level control of the grid/markup and like the snap-to-slot feel — at the cost
of owning the time axis and lane math. (DayPilot Scheduler — see `../FRONTEND_RESEARCH.md`
— would do overlap-stacking natively too, but verify its free Lite tier before committing.)
