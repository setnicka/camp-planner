// Camp Planner: timeline hydrator + editor.
//
// Reads the JSON the server inlined in #cp-timeline-data (already sliced into
// per-day-row segments by services.timeline.build_timeline) and renders it with
// vis-timeline; the day/window math is done server-side. When the server also embeds
// #cp-timeline-edit (i.e. the user can edit), setupEditing() adds drag/resize, the
// double-tap add flow with an activity picker, delete, undo/redo and a batched save.
"use strict";

(function () {
  const dataEl = document.getElementById("cp-timeline-data");
  const container = document.getElementById("cp-timeline");
  if (!dataEl || !container) return;

  const payload = JSON.parse(dataEl.textContent);
  const { el, withId, actionGroup } = window.cpDom;
  const camp = payload.camp;
  const DAY_MIN = 24 * 60;
  const WINDOW_START = camp.window_start_min;
  const WINDOW_END = WINDOW_START + DAY_MIN;

  const ROLE_LABEL = { main: "hlavní program", prep: "příprava", cleanup: "úklid" };
  // What a slot is called: main → bare title, prep/cleanup → "Role: title".
  const roleHeading = (role, title) => role === "main" ? title : `${ROLE_LABEL[role]}: ${title}`;

  // --- display filter (dim non-matching slots; deep-linkable via #filter=type:value) ----------
  // Display-only: filtered-out slots stay visible (and fully editable), just faded. `filter` is
  // null when off; segMatches() decides which segments stay bright and applyHeights() bakes the
  // `cp-dim` class onto the rest, so it survives vis redraws and the editor's restyles.
  // type: "activity" | "category" | "garant" (garants+helpers) | "attending" (slot attendees)
  let filter = null;   // { type, value: string } | null
  function segMatches(s) {
    if (!filter) return true;
    if (filter.type === "activity") return s.activity_id === filter.id;
    if (filter.type === "category") return s.cat_key === filter.value;
    if (filter.type === "garant") return s.garants.includes(filter.id) || s.helpers.includes(filter.id);
    if (filter.type === "attending") return s.attending.includes(filter.id);
    return true;
  }

  // --- helpers ---------------------------------------------------------------

  const pad = (n) => String(n).padStart(2, "0");

  // Absolute camp-minute -> clock "HH:MM" (mod the 24h day).
  function fmtClock(absMin) {
    const t = ((Math.round(absMin) % DAY_MIN) + DAY_MIN) % DAY_MIN;
    return pad(Math.floor(t / 60)) + ":" + pad(t % 60);
  }

  // White or the page's dark text (box colours ignore the theme), whichever wins by APCA
  // contrast; WCAG 2 contrast overrates dark text on mid tones.
  const DARK_TEXT = "#333333";
  function apcaY(rgb) {   // "rrggbb" -> APCA screen luminance, soft-clamped near black
    const n = parseInt(rgb, 16);
    const c = (v) => (v / 255) ** 2.4;
    const y = 0.2126729 * c((n >> 16) & 255) + 0.7151522 * c((n >> 8) & 255) + 0.072175 * c(n & 255);
    return y > 0.022 ? y : y + (0.022 - y) ** 1.414;
  }
  const DARK_Y = apcaY(DARK_TEXT.slice(1));
  function textColor(hex) {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
    if (!m) return "#fff";
    const bg = apcaY(m[1]);
    const dark = bg ** 0.56 - DARK_Y ** 0.57;     // dark text on the box
    const light = 1 - bg ** 0.65;                  // white text on the box
    return dark >= light ? DARK_TEXT : "#fff";
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"]/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  // Shared reference midnight (the day is the GROUP, not the date); vis renders the
  // axis in local time so item timestamps must be local too.
  const [Y, Mo, D] = camp.start_date.split("-").map(Number);
  const REF = new Date(Y, Mo - 1, D, 0, 0).getTime();
  const winStart = REF + WINDOW_START * 60000;
  const winEnd = REF + WINDOW_END * 60000;
  const mToDate = (minFromWindow) => new Date(winStart + minFromWindow * 60000);
  const DAY_MS = DAY_MIN * 60000;
  // day-row whose window holds an instant on the axis (rows roll over at the window start)
  const dayOf = (t) => Math.floor((t - winStart) / DAY_MS);
  const inCamp = (day) => day >= 0 && day < camp.length_days;

  // --- shared with the iCal export (window.cpTimelineKit) ---------------------

  // The category of an uncategorised slot (its segments carry cat_key "_none").
  const NO_CATEGORY = { key: "_none", label: "Bez kategorie", color: "#9e9e9e" };

  // Org chips that cycle garant/pomocník → účast na slotu → off, and a label naming the
  // active relation. A click calls onChange(f), f = { type: "garant" | "attending", id }
  // or null; set(f) shows a state set from outside.
  const ORG_MODE = { garant: "garant/pomocník", attending: "účast na slotu" };
  function orgCycle(orgs, onChange) {
    let cur = null;
    const label = el("span", { class: "cp-tl-orgmode" });
    const chips = orgs.map((o) => el("button", { type: "button", class: "cp-tl-chip", title: o.name,
      onclick: () => {
        const next = cur?.id !== o.id ? { type: "garant", id: o.id }
          : cur.type === "garant" ? { type: "attending", id: o.id } : null;
        set(next);
        onChange(next);
      } }, o.initials));
    function set(f) {
      cur = f;
      chips.forEach((c, i) => {
        const on = !!f && f.id === orgs[i].id;
        c.classList.toggle("on", on);
        c.classList.toggle("mode-garant", on && f.type === "garant");          // reddish
        c.classList.toggle("mode-attending", on && f.type === "attending");    // blueish
      });
      label.textContent = f ? ORG_MODE[f.type] : "";
      label.className = "cp-tl-orgmode" + (f ? " mode-" + f.type : "");
    }
    set(null);
    return { node: el("div", { class: "cp-tl-fgroup" }, ...chips, label), set };
  }
  window.cpTimelineKit = { NO_CATEGORY, orgCycle };

  // --- per-category colours + legend ----------------------------------------

  const cats = [...payload.categories, NO_CATEGORY];
  const styleRules = cats
    .map((c) => `#cp-timeline .vis-item.cat-${c.key}{background-color:${c.color};color:${textColor(c.color)}}`)
    .join("");

  // Constructed sheet, not a <style> element: an embedding host's CSP may forbid inline
  // styles, which blocks a <style> we inject but not the CSSOM.
  const sheet = new CSSStyleSheet();
  sheet.replaceSync(styleRules);
  document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];

  // The legend doubles as the category filter: each entry is a button carrying its
  // "category:<key>" token (setupFilter wires the clicks). A "Bez kategorie" entry is
  // added only when some slot is uncategorised.
  const legendItem = (c) => el("button",
    { type: "button", class: "cp-tl-legend-item", "data-filter": "category:" + c.key,
      title: "Filtrovat podle kategorie" },
    el("i", { style: "background:" + c.color }), c.label);
  const legend = el("div", { class: "cp-tl-legend" },
    el("span", { class: "cp-tl-filter-label" }, "Kategorie:"),
    ...payload.categories.map(legendItem),
    payload.segments.some((s) => s.cat_key === "_none") ? legendItem(NO_CATEGORY) : null);
  const filtersEl = document.querySelector(".cp-tl-filters");
  filtersEl.querySelector(".cp-tl-facets").append(legend);

  // --- groups (day rows) -----------------------------------------------------

  const CZ_WEEKDAYS = ["Ne", "Po", "Út", "St", "Čt", "Pá", "So"]; // by getUTCDay(): 0 = Sunday

  // Format a row label from its ISO date. Parse as UTC (split, not new Date(str)) so the
  // weekday can't roll a day in a negative-offset browser timezone.
  function dayLabel(iso) {
    const [y, m, d] = iso.split("-").map(Number);
    const weekday = CZ_WEEKDAYS[new Date(Date.UTC(y, m - 1, d)).getUTCDay()];
    return `${weekday}<span class="day-dom">${d}. ${m}.</span>`;
  }

  const groups = new vis.DataSet(
    // always a class: vis throws on className "" (see markToday)
    payload.groups.map((g) => ({ id: g.id, content: dayLabel(g.iso_date), className: "cp-day" }))
  );

  // --- items (program segments) ---------------------------------------------

  // unique id per rendered piece (the vis item id; no DB meaning)
  payload.segments.forEach((s, idx) => { s.idx = idx; });

  // org id -> {id, initials, name}; segments carry only ids (see payload.orgs).
  const orgById = Object.fromEntries(payload.orgs.map((o) => [o.id, o]));
  const initials = (id) => escapeHtml(orgById[id]?.initials ?? "?");

  // Inner HTML of one segment box. Pulled out so the editor can rebuild it after a
  // slot-attendee change (re-running it with the segment's updated `attending`).
  function segmentContent(s) {
    const heading = roleHeading(s.role, s.override_name || s.title);
    const left = s.cont_back ? "«&nbsp;" : "";
    const right = s.cont_fwd ? "&nbsp;»" : "";
    // garants and helpers bold (the card names their roles), attendees plain after a pipe
    const team = [...s.garants, ...s.helpers].map(initials).join(", ");
    const who = [team && `<b>${team}</b>`, s.attending.map(initials).join(", ")]
      .filter(Boolean).join(" | ");
    const orgs = who && `<span class="ev-orgs"><span class="ev-sep"> | </span>${who}</span>`;
    const when = `${fmtClock(s.abs_start_min)}–${fmtClock(s.abs_end_min)}`;
    const orgIds = [...new Set([...s.garants, ...s.helpers, ...s.attending])].join(",");
    // data-* attributes are the future filter hook (toggle opacity, no refetch).
    return `<div class="ev" data-activity-id="${s.activity_id}" data-slot-id="${s.slot_id}"` +
      ` data-cat="${s.cat_key}" data-tags="${s.tag_ids.join(",")}" data-org-ids="${orgIds}">` +
      `<div class="ev-title">${left}${escapeHtml(heading)}${right}</div>` +
      `<div class="ev-meta"><span class="ev-time" data-id="${s.idx}">${when}</span>${orgs}</div></div>`;
  }

  // The card under a selected slot: heading + clock, then full org names grouped by role
  // (empty groups omitted). Shown for every slot, even one with no orgs assigned yet.
  function segmentCard(s) {
    const names = (ids) => ids.map((id) => escapeHtml(orgById[id]?.name ?? "?")).join(", ");
    const when = `${fmtClock(s.abs_start_min)}–${fmtClock(s.abs_end_min)}`;
    const lines = [
      `<b>${escapeHtml(roleHeading(s.role, s.override_name || s.title))}</b>`,
      when,
    ];
    if (s.garants.length) lines.push(`<b>Garant:</b> ${names(s.garants)}`);
    if (s.helpers.length) lines.push(`<b>Pomocník:</b> ${names(s.helpers)}`);
    if (s.attending.length) lines.push(`<b>Účastní se:</b> ${names(s.attending)}`);
    return lines.join("<br>");
  }

  // base class (everything but `solo`); `solo` = double height is toggled live by
  // applyHeights() as drags change which boxes overlap within a row. Pulled out so the
  // editor can recompute it after a slot's role changes (prep/cleanup gain `margin`).
  function segmentBase(s) {
    return "cat-" + s.cat_key +
      (s.role !== "main" ? " margin" : "") +
      (s.cont_back ? " cut-l" : "") +
      (s.cont_fwd ? " cut-r" : "");
  }

  // One vis item per segment. Pulled out so a post-save refresh can rebuild the DataSet
  // from a fresh payload without a page reload (see rehydrate).
  function buildItem(s) {
    const base = segmentBase(s);
    return {
      id: s.idx,
      group: s.day,
      start: mToDate(s.rel_start_min),
      end: mToDate(s.rel_end_min),
      content: segmentContent(s),
      className: base,     // `solo` (double height) is added by applyHeights()
      slotId: s.slot_id,   // editing maps a vis item back to its slot (Phase 2)
      role: s.role,
      _seg: s,             // the source segment, for attendee re-render in the editor
      _base: base,         // class without `solo`, for live height recompute
      _title: s.title,     // for the change-log labels
    };
  }

  const items = new vis.DataSet(payload.segments.map(buildItem));

  // Half-height when a box overlaps another in its row, double-height when solo.
  // Called once now and re-run live during editing as drags change overlaps;
  // background items (no _base) are skipped. Also fades the activities other than the
  // selected one (cp-unfocus); the selected activity is never dimmed by the filter.
  let selActivity = null;   // activity of the selected slot (see selectionChanged)
  function applyHeights() {
    const rows = {};
    items.get().forEach((it) => { if (it._base != null) (rows[it.group] ??= []).push(it); });
    const updates = [];
    Object.values(rows).forEach((arr) => {
      const over = new Set();
      for (let i = 0; i < arr.length; i++)
        for (let j = i + 1; j < arr.length; j++)
          if (arr[i].start < arr[j].end && arr[j].start < arr[i].end) { over.add(arr[i].id); over.add(arr[j].id); }
      arr.forEach((it) => {
        const mine = selActivity != null && it._seg?.activity_id === selActivity;
        // one fade at most: the display filter's, else stepping back from the selection
        const fade = !mine && it._seg && !segMatches(it._seg) ? " cp-dim"
          : selActivity != null && !mine ? " cp-unfocus" : "";
        const cls = it._base + (over.has(it.id) ? "" : " solo") + fade;
        if (cls !== it.className) updates.push({ id: it.id, className: cls });
      });
    });
    if (updates.length) items.update(updates);
  }
  applyHeights();

  // --- timeline (read-only) --------------------------------------------------

  // A phone opens on NARROW_HOURS of the day: around now during the camp, else from the first
  // program, on a whole hour so the axis starts with a label. Passed to the constructor, as vis
  // resets an earlier setWindow.
  const NARROW_HOURS = 6;
  function initialWindow() {
    if (!window.matchMedia("(max-width: 40rem)").matches) return [winStart, winEnd];
    const span = NARROW_HOURS * 3600000;
    const now = campNowOnAxis(), day = dayOf(now);
    let from;
    if (inCamp(day)) {
      const back = now - day * DAY_MS - 3600000;   // an hour back, the rest ahead
      from = REF + Math.floor((back - REF) / 3600000) * 3600000;
    } else {
      const first = Math.min(...payload.segments.filter((s) => !s.cont_back).map((s) => s.rel_start_min));
      from = Number.isFinite(first) ? REF + Math.floor((WINDOW_START + first) / 60) * 3600000 : winStart;
    }
    from = Math.max(winStart, Math.min(from, winEnd - span));
    return [from, from + span];
  }
  const [initStart, initEnd] = initialWindow();

  const timeline = new vis.Timeline(container, items, groups, {
    stack: true,
    stackSubgroups: false,
    // Lane order, explicit so it can't ride on the order segments arrive in. vis puts the
    // first-sorted item in the bottom lane: earliest start lowest, longest first on equal
    // starts, unsaved slots (no slotId) on top.
    order: (a, b) => (a.start - b.start) || (b.end - a.end)
      || ((a.slotId ?? Infinity) - (b.slotId ?? Infinity)),
    groupOrder: "id",
    orientation: { axis: "both" },
    min: winStart, max: winEnd,
    start: initStart, end: initEnd,
    editable: false,
    itemsAlwaysDraggable: { item: false, range: false },
    xss: { disabled: true },   // our content is trusted server HTML; keep class/data-* attrs
    margin: { item: { horizontal: 0, vertical: 0 }, axis: 0 },
    zoomMin: 1000 * 60 * 60 * 2,
    showCurrentTime: false,
    showMajorLabels: false,
    format: { minorLabels: { hour: "HH:mm" } },
    zoomKey: "ctrlKey",
    // Shift + wheel pans; a plain wheel is left to the page. vis consults
    // horizontalScrollKey only when verticalScroll is on; inert here, the grid auto-sizes.
    horizontalScroll: true,
    horizontalScrollKey: "shiftKey",
    verticalScroll: true,
    align: "left",   // pin the label to its box; vis's default slides it along the left edge
    snap: (date) => {
      const ms = camp.snap_minutes * 60 * 1000;
      return new Date(Math.round(date / ms) * ms);
    },
  });

  window.cpTimeline = timeline; // for debugging in the console

  // --- selection --------------------------------------------------------------
  // A tap selects, a second tap or Escape deselects; every way the selection goes ends in
  // selectionChanged.
  let lastSel = null;
  const selectedItem = () => {
    const [id] = timeline.getSelection();
    return id != null ? items.get(id) : null;
  };
  function selectionChanged() {
    lastSel = timeline.getSelection()[0] ?? null;
    const act = selectedItem()?._seg?.activity_id ?? null;
    if (act !== selActivity) { selActivity = act; applyHeights(); }
    if (lastSel != null) showBar(); else hideBar();
  }
  function clearSelection() {
    timeline.setSelection([]);
    selectionChanged();
  }
  timeline.on("select", (props) => {
    // only a tap toggles: vis also selects on a long press, which starts a touch drag
    if (props.event?.type === "tap" && props.items.length === 1 && props.items[0] === lastSel) {
      timeline.setSelection([]);
    }
    selectionChanged();
  });
  // vis drops a removed item from the selection without a `select` event
  items.on("remove", () => { if (lastSel != null && !timeline.getSelection().length) clearSelection(); });
  document.addEventListener("keydown", (e) => {
    // runs before a dialog's own Escape, so an open dialog is still in the DOM
    if (e.key === "Escape" && lastSel != null && !document.querySelector(".cp-modal-overlay")) {
      clearSelection();
    }
  });

  // vis keeps its root hidden until a `changed` handler sees this flag, so the
  // grid stays blank until the 1 s autoResize poll. Pre-setting it reveals the
  // grid with the rest of the page (safe because Range applies start/end
  // synchronously, only the event is debounced).
  timeline.initialRangeChangeDone = true;

  // Vertical line at midnight: the day boundary that falls inside the window when it opens
  // at e.g. 04:00. One marker spans every row (all days map onto the same 24h window). Skip
  // it if the window opens at midnight (the line would sit on the left edge).
  const midnightMin = (DAY_MIN - WINDOW_START) % DAY_MIN;
  if (midnightMin > 0) timeline.addCustomTime(mToDate(midnightMin), "midnight");

  // --- day/night background gradient -----------------------------------------
  // Shade each day row by sun altitude (SunCalc-derived), using the camp's location.
  // No location set -> no gradient and no toggle (we don't guess coordinates). The
  // UTC offset is derived from the camp's timezone per day so DST is handled.
  const hasLocation = camp.latitude != null && camp.longitude != null;

  // Offset (ms) of the camp timezone at a given instant: how far local wall-clock
  // leads UTC. Computed via Intl; falls back to 0 for an unknown timezone.
  const tzFormats = {};   // building one is slow, and the now-line asks every redraw
  function tzOffsetMs(tz, date) {
    try {
      const f = tzFormats[tz] ??= new Intl.DateTimeFormat("en-US", {
        timeZone: tz, hour12: false, year: "numeric", month: "2-digit",
        day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
      });
      const p = {};
      for (const part of f.formatToParts(date)) p[part.type] = part.value;
      return Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute, +p.second) - date.getTime();
    } catch (_e) {
      return 0;
    }
  }

  const sunAltitude = (() => {
    const rad = Math.PI / 180, dayMs = 86400000, J1970 = 2440588, J2000 = 2451545, e = rad * 23.4397;
    const toDays = (d) => d / dayMs - 0.5 + J1970 - J2000;
    return (date, lat, lng) => {
      const lw = rad * -lng, phi = rad * lat, d = toDays(date);
      const M = rad * (357.5291 + 0.98560028 * d);
      const L = M + rad * (1.9148 * Math.sin(M) + 0.02 * Math.sin(2 * M) + 0.0003 * Math.sin(3 * M)) + rad * 102.9372 + Math.PI;
      const dec = Math.asin(Math.sin(e) * Math.sin(L));
      const ra = Math.atan2(Math.sin(L) * Math.cos(e), Math.cos(L));
      const H = rad * (280.16 + 360.9856235 * d) - lw - ra;
      return Math.asin(Math.sin(phi) * Math.sin(dec) + Math.cos(phi) * Math.cos(dec) * Math.cos(H));
    };
  })();
  // Night overlay: colour from a palette token, alpha from the sun. Resolved to a
  // literal rgba(): vis round-trips an item's style through the CSSOM, and a shorthand
  // holding var() gets dropped there.
  function nightRGB() {
    const v = getComputedStyle(container).getPropertyValue("--cp-daynight-rgb").trim();
    const n = v.split(/[\s,]+/).filter(Boolean).map(Number);   // Number("") is 0, not NaN
    if (n.length === 3 && n.every((x) => Number.isFinite(x))) return n.join(",");
    console.warn(`cp: --cp-daynight-rgb is ${v || "unset"}; expected an "r g b" triplet.`
      + " Skipping the day/night shading.");
    return null;
  }
  // peak night shading (--cp-daynight-max); unset or invalid means full strength
  function nightMax() {
    const v = parseFloat(getComputedStyle(container).getPropertyValue("--cp-daynight-max"));
    return Number.isFinite(v) ? Math.max(0, Math.min(1, v)) : 1;
  }
  function dnColor(altRad, rgb, max) {
    const a = (altRad * 180) / Math.PI;
    const t = Math.max(0, Math.min(1, (a + 12) / 15)); // 0 = night, 1 = day
    return `rgba(${rgb},${((1 - t) * max).toFixed(2)})`;
  }
  // Crescents and stars per day row, used as a mask: positions in % of the day keep them on
  // their hours on zoom, sizes in px keep them from stretching; seeded, so stable.
  function nightMotif(day) {
    let seed = (day + 1) * 2654435761 >>> 0;
    const rnd = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 2 ** 32);
    const at = (x, y, body) => `<svg x="${x.toFixed(2)}%" y="${y.toFixed(1)}%" overflow="visible">${body}</svg>`;
    const crescent = (r) => `<circle r="${r}" fill="#fff"/>` +
      `<circle cx="${r * .45}" cy="${-r * .35}" r="${r * .85}" fill="#000"/>`;
    const N = 60;   // one mark per 24 min of the day, nudged off the grid
    let marks = "";
    for (let k = 0; k < N; k++) {
      const x = (k + .2 + rnd() * .6) / N * 100, y = 15 + rnd() * 70, kind = rnd();
      marks += at(x, y, kind < .12 ? crescent(6) : kind < .22 ? crescent(4)
        : `<circle r="${(.7 + rnd() * .6).toFixed(2)}" fill="#fff"/>`);
    }
    return "url(\"data:image/svg+xml," + encodeURIComponent(
      '<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="100%"><mask id="m">' +
      '<rect width="100%" height="100%" fill="#000"/>' + marks +
      '</mask><rect width="100%" height="100%" mask="url(#m)"/></svg>') + "\")";
  }
  function dayNightBackgrounds() {
    const rgb = nightRGB();
    if (!rgb) return [];
    const max = nightMax();
    return payload.groups.flatMap((g, i) => {
      const midnightUTC = Date.UTC(Y, Mo - 1, D + i);
      const offMs = tzOffsetMs(camp.timezone, new Date(midnightUTC + 12 * 3600000)); // offset near local noon
      const alts = [];
      for (let m = 0; m <= DAY_MIN; m += 20) {
        const instant = new Date(midnightUTC + (WINDOW_START + m) * 60000 - offMs);
        alts.push({ alt: sunAltitude(instant, camp.latitude, camp.longitude), p: (m / DAY_MIN) * 100 });
      }
      const gradientOf = (scale) => {
        const samples = alts.map((a) => ({ c: dnColor(a.alt, rgb, scale), p: a.p }));
        // keep only stops where the colour changes (flat runs collapse to endpoints)
        const stops = samples
          .filter((s, k) => k === 0 || k === samples.length - 1 || s.c !== samples[k - 1].c || s.c !== samples[k + 1].c)
          .map((s) => `${s.c} ${s.p.toFixed(1)}%`);
        return `linear-gradient(to right, ${stops.join(",")})`;
      };
      const gradient = gradientOf(max);
      const span = { group: g.id, type: "background", start: winStart, end: winEnd,
        // limitSize:false stops vis clamping the box to ~3 panel-widths; the CSS gradient maps
        // `to right` across the box, so a clamped box would shift/squash it when zoomed in.
        limitSize: false };
      return [
        { ...span, id: "bg" + i, className: "cp-daynight", style: `background: ${gradient}` },
        // masked by the uncapped gradient: full strength where the light theme caps the shading
        { ...span, id: "ns" + i, className: "cp-daynight cp-nightsky",
          style: `mask-image: ${nightMotif(i)}, ${gradientOf(1)}` },
      ];
    });
  }
  if (hasLocation) items.add(dayNightBackgrounds());

  // The colour is baked into each item's style, so re-derive it on a theme change:
  // the visitor flipping our switch, or the OS while on "auto".
  function refreshDayNight() {
    if (hasLocation) items.update(dayNightBackgrounds());
  }
  window.addEventListener("cp:themechange", refreshDayNight);
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", refreshDayNight);

  // --- current-time line (today's row only, drawn over the items) ------------
  // Days are rows on one shared 24h window axis, so vis's full-height current-time bar would
  // cross every day, and a vis background item sits *behind* the event boxes. So we overlay our
  // own thin line on the center panel at "now", clipped to just today's row. Repositioned on
  // every redraw (zoom/pan/resize) and once a minute; hidden when the camp isn't running today.
  const nowLine = document.createElement("div");
  nowLine.className = "cp-nowline";
  nowLine.hidden = true;
  let nowDay = null;   // day-row the line last sat on; a change means snap (rollover), not slide
  // "Now" positioned on the axis in the camp's wall clock, not the viewer's: the axis renders
  // browser-local, so shift the real instant by (camp offset − browser offset). Identical when
  // the viewer sits in the camp timezone.
  function campNowOnAxis() {
    const now = Date.now();
    const d = new Date(now);
    const campOff = tzOffsetMs(camp.timezone, d);
    const browserOff = -d.getTimezoneOffset() * 60000;
    return now + campOff - browserOff;
  }
  // today's row label (cp-today), set as the group's className so vis keeps it across redraws
  let todayId = null;
  function markToday(day) {
    const id = payload.groups[day]?.id ?? null;
    if (id === todayId) return;
    const updates = [];
    if (todayId != null) updates.push({ id: todayId, className: "cp-day" });
    if (id != null) updates.push({ id, className: "cp-today" });
    todayId = id;
    groups.update(updates);
  }
  // animate=true (the minute tick) glides the line to its new spot; redraws / zoom / pan /
  // resize / the day rollover snap instantly (animating those would lag or slide backwards).
  function placeNowLine(center, animate) {
    if (nowLine.parentNode !== center) center.appendChild(nowLine);
    const now = campNowOnAxis();
    const day = dayOf(now);
    markToday(day);
    const groupEl = container.querySelectorAll(".vis-foreground .vis-group")[day];
    const win = timeline.getWindow();
    const nowOnAxis = now - day * DAY_MS;                       // fold now back onto the day-0 window axis
    const x = ((nowOnAxis - win.start.getTime()) / (win.end.getTime() - win.start.getTime())) * center.clientWidth;
    if (!inCamp(day) || !groupEl || x < 0 || x > center.clientWidth) {
      nowLine.hidden = true;
      nowDay = null;
      return;
    }
    const cr = center.getBoundingClientRect(), gr = groupEl.getBoundingClientRect();
    nowLine.classList.toggle("cp-snap", !(animate && day === nowDay));  // glide only the minute drift
    nowLine.hidden = false;
    nowLine.style.left = x + "px";
    nowLine.style.top = (gr.top - cr.top - 1) + "px";
    nowLine.style.height = gr.height + "px";
    nowDay = day;
  }

  // --- more of the day off screen: a shade on the edge it lies past --------------
  const moreLeft = el("div", { class: "cp-more cp-more-l" });
  const moreRight = el("div", { class: "cp-more cp-more-r" });
  function markMore(center) {
    if (moreLeft.parentNode !== center) center.append(moreLeft, moreRight);
    const w = timeline.getWindow(), slack = 60000;   // a minute's leeway for rounding
    moreLeft.hidden = w.start.getTime() <= winStart + slack;
    moreRight.hidden = w.end.getTime() >= winEnd - slack;
  }

  const redraw = (animate) => {
    const center = container.querySelector(".vis-panel.vis-center");
    if (center) { placeNowLine(center, animate === true); markMore(center); }
  };
  redraw();
  timeline.on("changed", redraw);   // after every vis redraw: zoom, pan, data/height changes
  window.addEventListener("resize", redraw);
  setInterval(() => redraw(true), 15000);

  // --- controls (day/night toggle, hint toggle, zoom) -------------------------
  const dnBtn = document.getElementById("cp-dn-toggle");
  if (dnBtn && !hasLocation) {
    dnBtn.hidden = true; // no coordinates -> day/night shading unavailable
  } else if (dnBtn) {
    dnBtn.classList.add("on"); // shading starts visible
    dnBtn.addEventListener("click", () => {
      const hidden = container.classList.toggle("dn-hidden");
      dnBtn.classList.toggle("on", !hidden);
    });
  }
  // a toggle button folding a panel (cp-open) on a narrow screen
  function disclose(btn, panel, onToggle) {
    if (btn && panel) btn.addEventListener("click", () => {
      const open = panel.classList.toggle("cp-open");
      btn.setAttribute("aria-expanded", open);
      onToggle(open);
    });
  }
  const helpBtn = document.getElementById("cp-help-toggle");
  disclose(helpBtn, document.getElementById("cp-tl-help"), (open) => helpBtn.classList.toggle("on", open));
  const zoomIn = document.getElementById("cp-zoom-in");
  const zoomOut = document.getElementById("cp-zoom-out");
  if (zoomIn) zoomIn.addEventListener("click", () => timeline.zoomIn(0.4));
  if (zoomOut) zoomOut.addEventListener("click", () => timeline.zoomOut(0.4));

  // --- filter control (clickable legend + org chips + activity picker) -------
  // The category facet IS the legend (wired here). After it come an activity picker (long list
  // → a select) and the org initial-chips (orgCycle), each facet a group that wraps whole.
  // Only one facet is active at a time; every state is a "type:value" token, also the #filter=
  // hash payload, so applying / reading / deep-linking share one mapping.
  // Rebuilds the activity-dependent facets from payload.segments; reassigned by setupFilter
  // and called by rehydrate after a save (a new activity may have appeared). No-op until then.
  let refreshFilterFacets = () => {};
  (function setupFilter() {
    const orgs = payload.orgs.slice().sort((a, b) => a.initials.localeCompare(b.initials, "cs"));
    const facets = [];   // the picker and org groups, after the legend
    const group = (cls, ...kids) => el("div", { class: "cp-tl-fgroup " + cls }, ...kids);
    const fLabel = (text) => el("span", { class: "cp-tl-filter-label" }, text);

    // The current time in the camp timezone (in the toolbar), with the tz name in small grey,
    // only when that differs from the viewer's own, i.e. when the wall clock is ambiguous.
    // Intl does the tz math.
    let browserTz = "";
    try { browserTz = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (_e) { /* leave "" */ }
    if (camp.timezone && camp.timezone !== browserTz) {
      const clockTime = el("b", { class: "cp-tl-clock-time" });
      document.querySelector(".cp-tl-view")?.append(
        el("span", { class: "cp-tl-clock", title: "Aktuální čas v časovém pásmu tábora" },
          "🕒 ", clockTime, el("span", { class: "cp-tl-clock-tz" }, camp.timezone)));
      const opts = { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false };
      let fmt;
      try { fmt = new Intl.DateTimeFormat("cs-CZ", { timeZone: camp.timezone, ...opts }); }
      catch (_e) { fmt = new Intl.DateTimeFormat("cs-CZ", opts); }   // unknown tz → viewer's
      const tickClock = () => { clockTime.textContent = fmt.format(new Date()); };
      tickClock();
      setInterval(tickClock, 1000);
    }

    // an activity is in the filter exactly when it has a segment; refreshFilterFacets fills it
    const actSel = payload.segments.length
      ? el("select", { class: "cp-tl-select" }, new Option("– vybrat hru –", ""))
      : null;
    if (actSel) facets.push(group("cp-tl-fact", fLabel("Hra:"), actSel));

    // shown only while a filter is on, its room kept so nothing reflows; at the end of the
    // org chips, in the room their last line leaves
    const clearBtn = el("button", { type: "button", class: "cp-mini cp-tl-clear cp-off" }, "Zrušit filtr");
    clearBtn.addEventListener("click", () => apply("", true));
    const fClear = document.getElementById("cp-filter-clear");   // beside the toggle on a phone
    fClear?.addEventListener("click", () => apply("", true));

    // org chips (each org listed once)
    const orgFacet = orgCycle(orgs, (f) => apply(f ? f.type + ":" + f.id : "", true));
    if (orgs.length) {
      orgFacet.node.classList.add("cp-tl-forg");
      orgFacet.node.prepend(fLabel("Org:"));
      orgFacet.node.append(clearBtn);
      facets.push(orgFacet.node);
    } else facets.push(clearBtn);
    legend.after(...facets);

    const catChips = [...legend.querySelectorAll("[data-filter]")];
    let VALID = new Set();
    let current = "";   // the active "type:value" token, "" = no filter
    const fToggle = document.getElementById("cp-filter-toggle");
    const filterName = () => filter.type === "category"
      ? catChips.find((c) => c.dataset.filter === current).textContent
      : filter.type === "activity" ? actSel?.selectedOptions[0]?.text ?? ""
        : `${orgById[filter.id].initials} (${ORG_MODE[filter.type]})`;
    // the folded filters still say what is on (lit by it), the arrow which way they go
    function labelToggle() {
      const arrow = filtersEl.classList.contains("cp-open") ? "▴" : "▾";
      fToggle.textContent = `Filtr${filter ? ": " + filterName() : ""} ${arrow}`;
      fToggle.classList.toggle("on", !!filter);
    }
    disclose(fToggle, filtersEl, labelToggle);
    function apply(token, updateHash) {
      const next = token && VALID.has(token) ? token : "";
      if (next === current) return;   // unchanged → skip the re-bake (also swallows our own hashchange echo)
      current = next;
      const i = current.indexOf(":");
      const value = current.slice(i + 1);
      filter = current ? { type: current.slice(0, i), value, id: Number(value) } : null;
      catChips.forEach((c) => c.classList.toggle("on", c.dataset.filter === current));
      orgFacet.set(filter && ORG_MODE[filter.type] ? filter : null);
      if (actSel) actSel.value = current.startsWith("activity:") ? current : "";
      clearBtn.classList.toggle("cp-off", !filter);
      fClear?.classList.toggle("cp-off", !filter);
      if (fToggle && filtersEl) labelToggle();
      applyHeights();              // re-bake cp-dim across all items
      if (updateHash) {
        if (filter) location.hash = "filter=" + filter.type + ":" + encodeURIComponent(filter.value);
        else if (location.hash) history.replaceState(null, "", location.pathname + location.search);
      }
    }
    function tokenFromHash() {
      const m = /^#filter=(activity|category|garant|attending):(.+)$/.exec(location.hash);
      return m ? `${m[1]}:${decodeURIComponent(m[2])}` : "";
    }

    // The activity <select> and the VALID tokens follow payload.segments (orgs and categories
    // don't change); the current selection stays while still valid.
    refreshFilterFacets = () => {
      const map = new Map();
      payload.segments.forEach((s) => { if (!map.has(s.activity_id)) map.set(s.activity_id, s.title); });
      const acts = [...map.entries()].sort((a, b) => a[1].localeCompare(b[1], "cs"));
      VALID = new Set([
        ...catChips.map((c) => c.dataset.filter),
        ...orgs.flatMap((o) => [`garant:${o.id}`, `attending:${o.id}`]),
        ...acts.map(([id]) => `activity:${id}`),
      ]);
      if (actSel) {
        const keep = actSel.value;
        actSel.length = 1;   // drop all but the "– vybrat hru –" placeholder
        acts.forEach(([id, title]) => actSel.add(new Option(title, `activity:${id}`)));
        actSel.value = VALID.has(keep) ? keep : "";
      }
      if (current && !VALID.has(current)) apply("", true);   // the filtered activity is gone
    };
    refreshFilterFacets();

    catChips.forEach((c) => c.addEventListener("click",
      () => apply(c.dataset.filter === current ? "" : c.dataset.filter, true)));   // re-click active = clear
    if (actSel) actSel.addEventListener("change", () => apply(actSel.value, true));
    window.addEventListener("hashchange", () => apply(tokenFromHash(), false));    // external links / back button
    apply(tokenFromHash(), false);   // initial state from the URL
  })();

  // Re-render from a fresh payload after a save without reloading: rebuild the items in
  // place, re-add day/night backgrounds, refresh the filter facets, take the new rev.
  function rehydrate(fresh) {
    payload.segments = fresh.segments;
    camp.rev = fresh.camp.rev;
    payload.segments.forEach((s, idx) => { s.idx = idx; });
    items.clear();
    items.add(payload.segments.map(buildItem));
    if (hasLocation) items.add(dayNightBackgrounds());
    applyHeights();
    refreshFilterFacets();
  }

  // --- floating actions over the selected slot, its card under it --------------
  // Rebuilt per selection from barActions (the editor sets its own); hidden while the
  // view moves, back once it settles, and following the slot when the page scrolls.
  const bar = el("div", { class: "cp-tl-actions", hidden: true });
  const card = el("div", { class: "cp-hint cp-tl-card", hidden: true });
  document.body.append(bar, card);
  let barActions = () => [];
  const openDetail = (it) => {
    const aid = it?._seg?.activity_id;
    if (aid != null) location.href = withId(container.dataset.activityDetail, aid);
  };
  const hideBar = () => { bar.hidden = true; card.hidden = true; };
  // above the selected box, the card under it (no room there: over the bar), kept to the grid's
  // visible part and out of sight with the box; reads, then writes
  function placeBar(sel) {
    const r = sel.getBoundingClientRect();
    const grid = container.querySelector(".vis-panel.vis-center").getBoundingClientRect();
    const [bw, bh, cw, ch] = [bar.offsetWidth, bar.offsetHeight, card.offsetWidth, card.offsetHeight];
    const off = r.bottom < 0 || r.top > window.innerHeight;
    bar.style.visibility = card.style.visibility = off ? "hidden" : "";
    const x = Math.max(r.left, grid.left);
    const leftOf = (w) => Math.max(4, Math.min(x, window.innerWidth - w - 4)) + "px";
    const barTop = Math.max(4, r.top - bh - 6), below = r.bottom + 6;
    Object.assign(bar.style, { left: leftOf(bw), top: barTop + "px" });
    Object.assign(card.style, { left: leftOf(cw),
      top: (below + ch <= window.innerHeight - 4 ? below : Math.max(4, barTop - ch - 6)) + "px" });
  }
  const selectedBox = () => container.querySelector(".vis-item.vis-selected");
  function showBar() {
    requestAnimationFrame(() => {
      const sel = selectedBox(), it = selectedItem();
      if (!sel || !it) return hideBar();
      bar.replaceChildren(actionGroup(barActions(it)));
      card.innerHTML = it._seg ? segmentCard(it._seg) : "";   // a placeholder has nothing to tell yet
      bar.hidden = false;
      card.hidden = !it._seg;
      placeBar(sel);
    });
  }
  // a page scroll or resize only moves them (once a frame), leaving the buttons under the finger
  let following = false;
  function follow() {
    if (bar.hidden || following) return;
    following = true;
    requestAnimationFrame(() => { following = false; const sel = selectedBox(); if (sel && !bar.hidden) placeBar(sel); });
  }
  timeline.on("rangechange", hideBar);   // pan/zoom slides the slot out from under it
  timeline.on("rangechanged", () => { if (lastSel != null) showBar(); });
  window.addEventListener("scroll", follow, { passive: true });
  window.addEventListener("resize", follow);
  // focus moving away from the timeline lets the selection go
  const dropSelection = () => { if (lastSel != null) clearSelection(); };
  document.addEventListener("pointerdown", (e) => {
    if (!container.contains(e.target) && !bar.contains(e.target)
        && !e.target.closest(".cp-modal-overlay")) dropSelection();   // a dialog acts on it
  });
  window.addEventListener("blur", dropSelection);

  // Present only when the server embedded the edit config (i.e. the user can edit).
  // Move/resize existing slots, double-tap to add (with an activity-picker modal),
  // tap-select + action bar to delete, all collected into a pending batch and
  // committed with one PATCH under the timeline_rev optimistic lock.
  const editEl = document.getElementById("cp-timeline-edit");
  if (editEl && window.cpTimelineEdit) {
    window.cpTimelineEdit({
      EDIT: JSON.parse(editEl.textContent),
      payload, camp, container, items, timeline,
      DAY_MIN, WINDOW_START, winStart, Y, Mo, D, ROLE_LABEL, roleHeading,
      fmtClock, mToDate, applyHeights, segmentContent, segmentBase,
      rehydrate, clearSelection, openDetail,
      setBarActions: (fn) => { barActions = fn; }, showBar, hideBar,
    });
  } else {
    barActions = (it) => [{ label: "ℹ️ Detail", onClick: () => openDetail(it) }];
  }
})();
