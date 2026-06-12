// Camp Planner — read-only timeline hydrator (Phase 1).
//
// Reads the JSON the server inlined in #cp-timeline-data (already sliced into
// per-day-row segments by services.timeline.build_timeline) and renders it with
// vis-timeline. Day/window math is done server-side; this file only maps segments
// to vis items and styles them.
//
// Phase 2 will add an edit mode: see the documented enableEditing() seam at the
// bottom — currently never called.
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

  // --- helpers ---------------------------------------------------------------

  // Absolute camp-minute -> clock "HH:MM" (mod the 24h day).
  function fmtClock(absMin) {
    const t = ((Math.round(absMin) % DAY_MIN) + DAY_MIN) % DAY_MIN;
    const pad = (n) => String(n).padStart(2, "0");
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
  document.head.insertAdjacentHTML("beforeend", "<style>" + styleRules + "</style>");

  const legend = document.createElement("div");
  legend.className = "cp-tl-legend";
  legend.innerHTML = payload.categories
    .map((c) => `<span><i style="background:${c.color}"></i>${escapeHtml(c.label)}</span>`)
    .join("");
  container.parentNode.insertBefore(legend, container);

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

  // Overlap within a row -> half height (stacks); a solo segment keeps double height.
  const overlaps = new Set();
  const byDay = {};
  payload.segments.forEach((s, idx) => {
    s.idx = idx; // unique id for this rendered piece (the vis item id; no DB meaning)
    (byDay[s.day] ??= []).push(s);
  });
  Object.values(byDay).forEach((arr) => {
    for (let i = 0; i < arr.length; i++)
      for (let j = i + 1; j < arr.length; j++)
        if (arr[i].rel_start_min < arr[j].rel_end_min && arr[j].rel_start_min < arr[i].rel_end_min) {
          overlaps.add(arr[i].idx);
          overlaps.add(arr[j].idx);
        }
  });

  // org id -> {id, initials, name}; segments carry only ids (see payload.orgs).
  const orgById = Object.fromEntries(payload.orgs.map((o) => [o.id, o]));
  const initials = (id) => escapeHtml(orgById[id]?.initials ?? "?");

  const items = new vis.DataSet(
    payload.segments.map((s) => {
      const heading = s.role === "main" ? s.title : `${ROLE_LABEL[s.role]}: ${s.title}`;
      const left = s.cont_back ? "«&nbsp;" : "";
      const right = s.cont_fwd ? "&nbsp;»" : "";
      // garants (bold) + helpers (normal), then any slot attendees in italics after a dot.
      const people = [
        ...s.garants.map((id) => `<b>${initials(id)}</b>`),
        ...s.helpers.map((id) => initials(id)),
      ].join(", ");
      const attending = s.attending.length
        ? `${people ? " · " : ""}<i>${s.attending.map(initials).join(", ")}</i>` : "";
      const orgs = s.role === "main" && (people || attending)
        ? ` · <span class="ev-orgs">${people}${attending}</span>` : "";
      const when = `${fmtClock(s.abs_start_min)}–${fmtClock(s.abs_end_min)}`;
      const orgIds = [...new Set([...s.garants, ...s.helpers, ...s.attending])].join(",");
      // data-* attributes are the future filter hook (toggle opacity, no refetch).
      const wrap =
        `<div class="ev" data-activity-id="${s.activity_id}" data-slot-id="${s.slot_id}"` +
        ` data-cat="${s.cat_key}" data-tags="${s.tag_ids.join(",")}" data-org-ids="${orgIds}">` +
        `<div class="ev-title">${left}${escapeHtml(heading)}${right}</div>` +
        `<div class="ev-meta"><span class="ev-time">${when}</span>${orgs}</div></div>`;
      const cls =
        "cat-" + s.cat_key +
        (overlaps.has(s.idx) ? "" : " solo") +
        (s.role !== "main" ? " margin" : "") +
        (s.cont_back ? " cut-l" : "") +
        (s.cont_fwd ? " cut-r" : "");
      return {
        id: s.idx,
        group: s.day,
        start: mToDate(s.rel_start_min),
        end: mToDate(s.rel_end_min),
        content: wrap,
        className: cls,
      };
    })
  );

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

  // Expose for debugging / Phase 2 wiring.
  window.cpTimeline = timeline;

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

  // --- Phase 2 seam (not wired in Phase 1) -----------------------------------
  // function enableEditing(timeline, ctx) { /* set editable, attach onMoving/onMove,
  //   collect moves into an undo stack (Back + Ctrl-Z), PUT them with `camp.rev`
  //   (optimistic-lock token), handle 409 by refetching and re-confirming. */ }
})();
