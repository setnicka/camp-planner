// Camp Planner — timeline hydrator + editor.
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

  // Readable text colour for a category background (white on dark, near-black on light).
  function textColor(hex) {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
    if (!m) return "#fff";
    const n = parseInt(m[1], 16);
    const r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
    const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
    return lum > 0.6 ? "#3c3c3c" : "#fff";
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

  // --- per-category colours + legend ----------------------------------------

  const cats = payload.categories.slice();
  cats.push({ key: "_none", label: "Bez kategorie", color: "#9e9e9e" });
  const styleRules = cats
    .map((c) => `#cp-timeline .vis-item.cat-${c.key}{background-color:${c.color};color:${textColor(c.color)}}`)
    .join("");

  // Row hover: highlight the hovered day row plus its left-panel label. The label lives
  // in a separate subtree, so pair it by position via :has() — one rule per day, generated
  // from the actual group count (no hardcoded cap on camp length).
  const HOVER_HI = "background:rgba(255,205,0,.15);box-shadow:inset 0 2px 0 #444,inset 0 -2px 0 #444";
  const hoverRules = "#cp-timeline .vis-foreground .vis-group:hover{" + HOVER_HI + "}" +
    payload.groups.map((g, i) =>
      `#cp-timeline:has(.vis-foreground .vis-group:nth-child(${i + 1}):hover) .vis-labelset .vis-label:nth-child(${i + 1}){${HOVER_HI}}`
    ).join("");
  document.head.insertAdjacentHTML("beforeend", "<style>" + styleRules + hoverRules + "</style>");

  // The legend doubles as the category filter: each entry is a button carrying its
  // "category:<key>" token (setupFilter wires the clicks). A "Bez kategorie" entry is
  // added only when some slot is uncategorised.
  const legend = document.createElement("div");
  legend.className = "cp-tl-legend";
  const legendItem = (key, color, text) =>
    `<button type="button" class="cp-tl-legend-item" data-filter="category:${key}" title="Filtrovat podle kategorie">` +
    `<i style="background:${color}"></i>${escapeHtml(text)}</button>`;
  legend.innerHTML = `<span class="cp-tl-filter-label">Kategorie:</span>` +
    payload.categories.map((c) => legendItem(c.key, c.color, c.label)).join("") +
    (payload.segments.some((s) => s.cat_key === "_none") ? legendItem("_none", "#9e9e9e", "Bez kategorie") : "");
  // stack under the title (left column), above the editor help text if present; bars
  // are the right column. Falls back to above the timeline if that container isn't present.
  const left = document.querySelector(".cp-tl-left");
  if (left) left.insertBefore(legend, left.querySelector(".cp-tl-help"));
  else container.parentNode.insertBefore(legend, container);

  // --- groups (day rows) -----------------------------------------------------

  const CZ_WEEKDAYS = ["Ne", "Po", "Út", "St", "Čt", "Pá", "So"]; // by getUTCDay(): 0 = Sunday

  // Format a row label from its ISO date. Parse as UTC (split, not new Date(str)) so the
  // weekday can't roll a day in a negative-offset browser timezone.
  function dayLabel(iso) {
    const [y, m, d] = iso.split("-").map(Number);
    const weekday = CZ_WEEKDAYS[new Date(Date.UTC(y, m - 1, d)).getUTCDay()];
    return `${weekday} <span class="day-dom">${d}. ${m}.</span>`;
  }

  const groups = new vis.DataSet(
    payload.groups.map((g) => ({ id: g.id, content: dayLabel(g.iso_date) }))
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
    const heading = roleHeading(s.role, s.title);
    const left = s.cont_back ? "«&nbsp;" : "";
    const right = s.cont_fwd ? "&nbsp;»" : "";
    // garants (bold) + helpers (normal), then any slot attendees in italics after a pipe.
    const people = [
      ...s.garants.map((id) => `<b>${initials(id)}</b>`),
      ...s.helpers.map((id) => initials(id)),
    ].join(", ");
    const attending = s.attending.length
      ? `${people ? " | " : ""}<i>${s.attending.map(initials).join(", ")}</i>` : "";
    const orgs = (people || attending)
      ? ` | <span class="ev-orgs">${people}${attending}</span>` : "";
    const when = `${fmtClock(s.abs_start_min)}–${fmtClock(s.abs_end_min)}`;
    const orgIds = [...new Set([...s.garants, ...s.helpers, ...s.attending])].join(",");
    // data-* attributes are the future filter hook (toggle opacity, no refetch).
    return `<div class="ev" data-activity-id="${s.activity_id}" data-slot-id="${s.slot_id}"` +
      ` data-cat="${s.cat_key}" data-tags="${s.tag_ids.join(",")}" data-org-ids="${orgIds}">` +
      `<div class="ev-title">${left}${escapeHtml(heading)}${right}</div>` +
      `<div class="ev-meta"><span class="ev-time" data-id="${s.idx}">${when}</span>${orgs}</div></div>`;
  }

  // Hover tooltip (vis `title`): full org names grouped by role; empty groups omitted.
  function segmentTitle(s) {
    if (!s.garants.length && !s.helpers.length && !s.attending.length) return ""; // no orgs → no tooltip
    const names = (ids) => ids.map((id) => escapeHtml(orgById[id]?.name ?? "?")).join(", ");
    const lines = [`<b>${escapeHtml(s.title)}</b>`];
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

  const items = new vis.DataSet(
    payload.segments.map((s) => {
      const base = segmentBase(s);
      return {
        id: s.idx,
        group: s.day,
        start: mToDate(s.rel_start_min),
        end: mToDate(s.rel_end_min),
        content: segmentContent(s),
        title: segmentTitle(s),   // hover tooltip: orgs by role (full names)
        className: base,     // `solo` (double height) is added by applyHeights()
        slotId: s.slot_id,   // editing maps a vis item back to its slot (Phase 2)
        role: s.role,
        _seg: s,             // the source segment, for attendee re-render in the editor
        _base: base,         // class without `solo`, for live height recompute
        _title: s.title,     // for the change-log labels
      };
    })
  );

  // Half-height when a box overlaps another in its row, double-height when solo.
  // Called once now and re-run live during editing as drags change overlaps;
  // background items (no _base) are skipped.
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
        const dim = it._seg && !segMatches(it._seg) ? " cp-dim" : "";   // display filter (fade non-matches)
        const cls = it._base + (over.has(it.id) ? "" : " solo") + dim;
        if (cls !== it.className) updates.push({ id: it.id, className: cls });
      });
    });
    if (updates.length) items.update(updates);
  }
  applyHeights();

  // --- timeline (read-only) --------------------------------------------------

  const timeline = new vis.Timeline(container, items, groups, {
    stack: true,
    stackSubgroups: false,
    groupOrder: "id",
    orientation: { axis: "both" },
    min: winStart, max: winEnd,
    start: winStart, end: winEnd,
    editable: false,
    itemsAlwaysDraggable: { item: false, range: false },
    xss: { disabled: true },   // our content is trusted server HTML; keep class/data-* attrs
    margin: { item: { horizontal: 0, vertical: 0 }, axis: 0 },
    zoomMin: 1000 * 60 * 60 * 2,
    showCurrentTime: false,
    showMajorLabels: false,
    format: { minorLabels: { hour: "HH:mm" } },
    zoomKey: "ctrlKey",
    snap: (date) => {
      const ms = camp.snap_minutes * 60 * 1000;
      return new Date(Math.round(date / ms) * ms);
    },
  });

  window.cpTimeline = timeline; // for debugging in the console

  // Vertical line at midnight — the day boundary that falls inside the window when it opens
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
  function tzOffsetMs(tz, date) {
    try {
      const f = new Intl.DateTimeFormat("en-US", {
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
  function dnColor(altRad) {
    const a = (altRad * 180) / Math.PI;
    const t = Math.max(0, Math.min(1, (a + 12) / 15)); // 0 = night, 1 = day
    return `rgba(24,34,62,${(1 - t).toFixed(2)})`;
  }
  function dayNightBackgrounds() {
    return payload.groups.map((g, i) => {
      const midnightUTC = Date.UTC(Y, Mo - 1, D + i);
      const offMs = tzOffsetMs(camp.timezone, new Date(midnightUTC + 12 * 3600000)); // offset near local noon
      const samples = [];
      for (let m = 0; m <= DAY_MIN; m += 20) {
        const instant = new Date(midnightUTC + (WINDOW_START + m) * 60000 - offMs);
        samples.push({ c: dnColor(sunAltitude(instant, camp.latitude, camp.longitude)), p: (m / DAY_MIN) * 100 });
      }
      // keep only stops where the colour changes (flat runs collapse to endpoints)
      const stops = samples
        .filter((s, k) => k === 0 || k === samples.length - 1 || s.c !== samples[k - 1].c || s.c !== samples[k + 1].c)
        .map((s) => `${s.c} ${s.p.toFixed(1)}%`);
      return {
        id: "bg" + i, group: g.id, type: "background", start: winStart, end: winEnd,
        className: "cp-daynight", style: `background: linear-gradient(to right, ${stops.join(",")})`,
      };
    });
  }
  if (hasLocation) items.add(dayNightBackgrounds());

  // --- controls (day/night toggle + zoom) ------------------------------------
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
  const zoomIn = document.getElementById("cp-zoom-in");
  const zoomOut = document.getElementById("cp-zoom-out");
  if (zoomIn) zoomIn.addEventListener("click", () => timeline.zoomIn(0.3));
  if (zoomOut) zoomOut.addEventListener("click", () => timeline.zoomOut(0.3));

  // --- filter control (clickable legend + org chips + activity picker) -------
  // The category facet IS the legend (wired here). Below it sit one row of org initial-chips and
  // an activity picker (long list → a select). Each org chip cycles garant/pomocník → účast na
  // slotu → off (a trailing label names the active relation). Only one facet is active at a time;
  // every state is a "type:value" token, also the #filter= hash payload, so applying / reading /
  // deep-linking share one mapping.
  const ORG_MODE = { garant: "garant/pomocník", attending: "účast na slotu" };
  (function setupFilter() {
    const legend = document.querySelector(".cp-tl-legend");
    const orgs = payload.orgs.slice().sort((a, b) => a.initials.localeCompare(b.initials, "cs"));
    const actMap = new Map();
    payload.segments.forEach((s) => { if (!actMap.has(s.activity_id)) actMap.set(s.activity_id, s.title); });
    const activities = [...actMap.entries()].sort((a, b) => a[1].localeCompare(b[1], "cs"));

    // org chips + the activity picker share one row
    const row = document.createElement("div");
    row.className = "cp-tl-frow";
    const fLabel = (text) => Object.assign(document.createElement("span"), { className: "cp-tl-filter-label", textContent: text });

    // org chips (each org listed once); click cycles garant → účast → off
    const orgChips = [];
    let modeLabel = null;
    if (orgs.length) {
      row.append(fLabel("Org:"));
      orgs.forEach((o) => {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "cp-tl-chip";
        chip.dataset.orgId = String(o.id);
        chip.title = o.name;            // full name on hover
        chip.textContent = o.initials;
        orgChips.push(chip);
        row.append(chip);
      });
      modeLabel = Object.assign(document.createElement("span"), { className: "cp-tl-orgmode" });
      row.append(modeLabel);
    }

    let actSel = null;
    if (activities.length) {
      actSel = document.createElement("select");
      actSel.className = "cp-tl-select";
      actSel.add(new Option("— vybrat hru —", ""));
      activities.forEach(([id, title]) => actSel.add(new Option(title, `activity:${id}`)));
      row.append(fLabel("Hra:"), actSel);
    }
    if (row.children.length) {   // skip an empty row (no orgs and no slotted activities)
      if (legend) legend.after(row);
      else (left || container.parentNode).insertBefore(row, container);
    }

    const catChips = legend ? [...legend.querySelectorAll("[data-filter]")] : [];
    const VALID = new Set([
      ...catChips.map((c) => c.dataset.filter),
      ...orgs.flatMap((o) => [`garant:${o.id}`, `attending:${o.id}`]),
      ...activities.map(([id]) => `activity:${id}`),
    ]);

    let current = "";   // the active "type:value" token, "" = no filter
    function apply(token, updateHash) {
      const next = token && VALID.has(token) ? token : "";
      if (next === current) return;   // unchanged → skip the re-bake (also swallows our own hashchange echo)
      current = next;
      const i = current.indexOf(":");
      const value = current.slice(i + 1);
      filter = current ? { type: current.slice(0, i), value, id: Number(value) } : null;
      const orgMode = filter && ORG_MODE[filter.type];   // active org relation, or undefined
      catChips.forEach((c) => c.classList.toggle("on", c.dataset.filter === current));
      orgChips.forEach((c) => {
        const active = !!orgMode && filter.value === c.dataset.orgId;
        c.classList.toggle("on", active);                                  // colour by relation:
        c.classList.toggle("mode-garant", active && filter.type === "garant");        // reddish
        c.classList.toggle("mode-attending", active && filter.type === "attending");  // blueish
      });
      if (modeLabel) {
        modeLabel.textContent = orgMode || "";
        modeLabel.className = "cp-tl-orgmode" + (orgMode ? " mode-" + filter.type : "");
      }
      if (actSel) actSel.value = current.startsWith("activity:") ? current : "";
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

    catChips.forEach((c) => c.addEventListener("click",
      () => apply(c.dataset.filter === current ? "" : c.dataset.filter, true)));   // re-click active = clear
    orgChips.forEach((c) => c.addEventListener("click", () => {
      const id = c.dataset.orgId;   // cycle: 1st click garant, 2nd účast, 3rd off
      const next = current === `garant:${id}` ? `attending:${id}` : current === `attending:${id}` ? "" : `garant:${id}`;
      apply(next, true);
    }));
    if (actSel) actSel.addEventListener("change", () => apply(actSel.value, true));
    window.addEventListener("hashchange", () => apply(tokenFromHash(), false));    // external links / back button
    apply(tokenFromHash(), false);   // initial state from the URL
  })();

  // --- editing (Phase 2) -----------------------------------------------------
  // Present only when the server embedded the edit config (i.e. the user can edit).
  // Move/resize existing slots, double-tap to add (with an activity-picker modal),
  // tap-select + action bar to delete — all collected into a pending batch and
  // committed with one PATCH under the timeline_rev optimistic lock.
  const editEl = document.getElementById("cp-timeline-edit");
  if (editEl && window.cpTimelineEdit) {
    window.cpTimelineEdit({
      EDIT: JSON.parse(editEl.textContent),
      payload, camp, container, items, timeline,
      DAY_MIN, WINDOW_START, winStart, Y, Mo, D, ROLE_LABEL, roleHeading,
      fmtClock, mToDate, escapeHtml, applyHeights, segmentContent, segmentTitle, segmentBase,
    });
  }
})();
