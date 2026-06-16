// Camp Planner — shared change-history feed ("Historie změn").
//
// GitLab-style audit feed rendered from the /api/.../audit endpoint: newest first, each
// entry an author + relative time + per-field old→new diffs, paginated with the keyset
// `next_before` cursor. Read-only — no edit affordances. Used by the activity detail page
// (filtered to one activity) and the camp settings page (high-level camp_level feed).
//
// Factory: window.cpHistoryFeed({ host, url, query, catById, typeLabels, emptyText }) → { reload }
//   host       element to render into (the feed <ol> + "load older" button live here)
//   url        audit endpoint base (no query string)
//   query      extra fixed query params, e.g. { activity_id: 5 } or { camp_level: true }
//   catById    { id: {name} } to resolve category_id values (optional)
//   typeLabels { enumValue: label } for activity `type` values (optional)
//   emptyText  message when the feed is empty (optional)
// reload() clears the feed and loads the newest page; "Načíst starší" appends older pages.
"use strict";

window.cpHistoryFeed = (function () {
  const { el, api, toast, plural } = window.cpDom;

  const SLOT_ROLE = { main: "Hlavní slot", prep: "Příprava", cleanup: "Úklid" };
  const ENTITY_LABELS = {
    activity: "aktivitu", slot: "slot", assignment: "organizátory", tag: "tagy",
    todo: "úkol", material: "materiál", material_need: "potřebu materiálu",
    category: "kategorii", org: "organizátora", camp: "akci", timeline: "harmonogram",
  };
  const ACTION_VERBS = { create: "vytvořil(a)", update: "upravil(a)", delete: "smazal(a)", merge: "sloučil(a)" };
  const GEN_LABELS = { activity: "jiné aktivity", material: "jiného materiálu" };  // genitive after "do" (merge)
  const FIELD_LABELS = {
    title: "Název", name: "Název", slug: "Identifikátor", category_id: "Kategorie",
    type: "Typ", description_md: "Popis", config: "Konfigurace", note: "Poznámka",
    url: "Odkaz", unit: "Jednotka", amount: "Množství", is_ready: "Připraveno",
    is_done: "Hotovo", due_date: "Termín", material: "Materiál", merged_from: "Sloučeno z",
    role: "Typ slotu", start_at: "Začátek", end_at: "Konec", garant: "Garant",
    orgs: "Organizátoři", google_calendar_id: "Google kalendář", items: "Položky",
    moved: "přesunuto", created: "vytvořeno", retyped: "přetypováno", deleted: "smazáno",
  };

  const DT_FMT = { day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit" };

  // Two distinct kinds of timestamp live in the feed:
  //  • created_at is an instant — a naive UTC timestamp (DB func.now()) serialized without a
  //    zone marker; asInstant marks it UTC so the browser shows it in the viewer's local zone
  //    instead of reading the UTC wall-clock as already-local (one UTC-offset in the past).
  //  • slot start_at/end_at and due_date are naive LOCAL wall-clock (the data model keeps them
  //    zone-free on purpose) — parse them as-is, never shifted.
  const asInstant = (iso) => new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z");
  const fmtInstant = (iso) => asInstant(iso).toLocaleString("cs-CZ", DT_FMT);
  const fmtWallClock = (iso) => new Date(iso).toLocaleString("cs-CZ", DT_FMT);

  // Relative "před …" for the last week, absolute timestamp beyond it (full ts always in title).
  // floor (not round) so it reads like elapsed time — "1 hodinou" until two full hours pass.
  function relTime(iso) {
    const s = Math.floor((Date.now() - asInstant(iso).getTime()) / 1000);
    if (s < 60) return "před chvílí";
    const m = Math.floor(s / 60);
    if (m < 60) return `před ${m} ${plural(m, "minutou", "minutami", "minutami")}`;
    const h = Math.floor(m / 60);
    if (h < 24) return `před ${h} ${plural(h, "hodinou", "hodinami", "hodinami")}`;
    const d = Math.floor(h / 24);
    if (d < 7) return `před ${d} ${plural(d, "dnem", "dny", "dny")}`;
    return fmtInstant(iso);
  }

  const CAP = 80;   // long string values collapse to this many chars until expanded
  const trunc = (s) => (s.length > CAP ? s.slice(0, CAP) + "…" : s);

  // Word-level diff via LCS over whitespace/word tokens (so spacing and line breaks survive).
  // Returns ops [{t:'eq'|'del'|'ins', s}]. Quadratic in token count — callers guard the size.
  function diffTokens(a, b) {
    const n = a.length, m = b.length;
    const dp = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
    for (let i = n - 1; i >= 0; i--)
      for (let j = m - 1; j >= 0; j--)
        dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    const ops = [];
    let i = 0, j = 0;
    while (i < n && j < m) {
      if (a[i] === b[j]) ops.push({ t: "eq", s: a[i++] }), j++;
      else if (dp[i + 1][j] >= dp[i][j + 1]) ops.push({ t: "del", s: a[i++] });
      else ops.push({ t: "ins", s: b[j++] });
    }
    while (i < n) ops.push({ t: "del", s: a[i++] });
    while (j < m) ops.push({ t: "ins", s: b[j++] });
    return ops;
  }

  // Coalesce diff ops of the same kind into runs: equal text as-is, removals in <del>,
  // additions in <ins>. Returns an array of nodes for a pre-wrap diff box.
  function opsToNodes(ops) {
    const nodes = [];
    let buf = "", type = null;
    const flush = () => {
      if (!buf) return;
      nodes.push(type === "eq" ? document.createTextNode(buf) : el(type, null, buf));
      buf = "";
    };
    for (const op of ops) { if (op.t !== type) { flush(); type = op.t; } buf += op.s; }
    flush();
    return nodes;
  }

  // Fraction of characters that changed (del+ins) over the whole diff. Near 1 means the text
  // was largely rewritten, where an inline word diff is just confetti from incidental matches.
  function changedRatio(ops) {
    let changed = 0, total = 0;
    for (const op of ops) { total += op.s.length; if (op.t !== "eq") changed += op.s.length; }
    return total ? changed / total : 0;
  }

  // The expanded view of a long change. With both sides present we attempt an inline word
  // diff, but only when it stays readable: too large for the quadratic diff, or more than ~half
  // the text changed (a rewrite, where alignment is noise) → fall back to full old/new blocks.
  // create/delete (one side) → a single full-text block.
  function expandedDiff(oldFull, newFull, showOld, showNew) {
    if (showOld && showNew) {
      const a = oldFull.match(/\s+|\S+/g) || [], b = newFull.match(/\s+|\S+/g) || [];
      if (a.length <= 1200 && b.length <= 1200) {
        const ops = diffTokens(a, b);
        if (changedRatio(ops) <= 0.5)
          return el("div", { class: "cp-hist-diffbox" }, ...opsToNodes(ops));
      }
      return el("div", { class: "cp-hist-diff" },
        el("div", { class: "cp-hist-diffbox cp-hist-oldblock" }, oldFull),
        el("div", { class: "cp-hist-diffbox cp-hist-newblock" }, newFull));
    }
    const cls = showNew ? "cp-hist-newblock" : "cp-hist-oldblock";
    return el("div", { class: "cp-hist-diffbox " + cls }, showNew ? newFull : oldFull);
  }

  return function cpHistoryFeed(opts) {
    const host = opts.host;
    const baseUrl = opts.url;
    const fixed = opts.query || {};
    const catById = opts.catById || {};
    const typeLabels = opts.typeLabels || {};
    const emptyText = opts.emptyText || "Zatím žádné změny.";

    // Full display form of a raw value (no truncation): booleans, category/type/role lookups
    // and wall-clock dates resolve to labels; objects (config, taxonomy item lists) to JSON;
    // everything else is its string form.
    function rawValue(field, v) {
      if (v === null || v === undefined || v === "") return "—";
      if (typeof v === "boolean") return v ? "ano" : "ne";
      if (field === "category_id") { const c = catById[v]; return c ? (c.label || c.name) : ("#" + v); }
      if (field === "type") return typeLabels[v] || v;
      if (field === "role") return SLOT_ROLE[v] || v;
      if (field === "start_at" || field === "end_at" || field === "due_date") return fmtWallClock(v);
      if (typeof v === "object") return JSON.stringify(v);   // config / taxonomy items → text
      return String(v);
    }

    // One field's change. Normally a [before, after] pair rendered old → new; the timeline
    // batch summary uses bare counts (shown only when nonzero). When either side is long
    // (description/config/long note edits) the row stays truncated until a "zobrazit více"
    // toggle reveals a word-level diff of the full text (built lazily on first expand).
    function changeRow(action, field, val) {
      const label = el("span", { class: "cp-hist-field" }, FIELD_LABELS[field] || field);
      if (!Array.isArray(val)) return val ? el("li", { class: "cp-hist-change" }, label, " " + val) : null;
      const [oldV, newV] = val;
      const empty = (x) => x === null || x === undefined || x === "";
      const showOld = !(action === "create" || empty(oldV));
      const showNew = !(action === "delete" || empty(newV));
      const oldFull = rawValue(field, oldV), newFull = rawValue(field, newV);
      const long = (showOld && oldFull.length > CAP) || (showNew && newFull.length > CAP);

      const oldNode = showOld ? el("del", { class: "cp-hist-old" }, long ? trunc(oldFull) : oldFull) : null;
      const newNode = showNew ? el("ins", { class: "cp-hist-new" }, long ? trunc(newFull) : newFull) : null;
      const arrow = (showOld && showNew) ? el("span", { class: "cp-hist-arrow" }, " → ") : null;
      const collapsed = el("span", { class: "cp-hist-vals" }, oldNode, arrow, newNode);
      if (!long) return el("li", { class: "cp-hist-change" }, label, " ", collapsed);

      let expanded = false, built = false;
      const diffHost = el("div", { class: "cp-hist-diff", hidden: true });
      const toggle = el("button", { type: "button", class: "cp-hist-toggle" }, "zobrazit více");
      toggle.addEventListener("click", () => {
        expanded = !expanded;
        if (expanded && !built) { built = true; diffHost.append(expandedDiff(oldFull, newFull, showOld, showNew)); }
        collapsed.hidden = expanded;
        diffHost.hidden = !expanded;
        toggle.textContent = expanded ? "zobrazit méně" : "zobrazit více";
      });
      return el("li", { class: "cp-hist-change cp-hist-long" }, label, " ", toggle, collapsed, diffHost);
    }

    // Headline noun for an entry. An activity can have several slots, so identify a slot by
    // its role label (taken from the diff when it carries one — create/retype do) plus its id,
    // e.g. "Hlavní slot (#12)"; falls back to "slot (#12)" for moves/deletes that omit the role.
    function entityLabel(e, changes) {
      if (e.entity_type === "slot" && e.entity_id != null) {
        const r = changes.role;
        const role = Array.isArray(r) ? r[1] : r;
        return (role ? (SLOT_ROLE[role] || role) : "slot") + " (#" + e.entity_id + ")";
      }
      return ENTITY_LABELS[e.entity_type] || e.entity_type;
    }

    // entity_url is set server-side only when the target activity/material still exists.
    const linked = (text, url) => (url ? el("a", { class: "cp-hist-link", href: url }, text) : text);

    // The headline text + entity node(s). When the entity still exists the server sends its
    // name (entity_title) — use it instead of the generic noun. A merge reads as a direction:
    // "sloučil(a) aktivitu „Source" do <target name>", target linked.
    function headParts(e, changes) {
      const verb = ACTION_VERBS[e.action] || e.action;
      if (e.action === "merge" && Array.isArray(changes.merged_from)) {
        const acc = ENTITY_LABELS[e.entity_type] || e.entity_type;
        const gen = GEN_LABELS[e.entity_type] || acc;
        return [`${verb} ${acc} „${changes.merged_from[0]}“ do `, linked(e.entity_title || gen, e.entity_url)];
      }
      return [verb + " ", linked(e.entity_title || entityLabel(e, changes), e.entity_url)];
    }

    function entryNode(e) {
      const changes = e.changes || {};
      // merged_from is folded into the merge headline above, so drop it from the field rows.
      const rows = Object.keys(changes)
        .filter((f) => !(e.action === "merge" && f === "merged_from"))
        .map((f) => changeRow(e.action, f, changes[f])).filter(Boolean);
      // On a whole-camp feed, a per-activity detail entry (slot/todo/…) carries its parent
      // activity (set server-side); show it as a context line linking to the activity. The
      // activity feed itself never sets activity_url, so this stays absent there.
      const actRef = e.activity_url
        ? el("div", { class: "cp-hist-actref" },
          el("span", { class: "cp-hist-field" }, "Aktivita"), " ",
          el("a", { class: "cp-hist-link", href: e.activity_url }, e.activity_title))
        : null;
      return el("li", { class: "cp-hist-entry cp-hist-" + e.action },
        el("span", { class: "cp-hist-dot" }),
        el("div", { class: "cp-hist-body" },
          el("div", { class: "cp-hist-head" },
            el("span", { class: "cp-hist-author" }, e.author || "systém"),
            " ", ...headParts(e, changes), " ",
            el("time", { class: "cp-hist-time", title: fmtInstant(e.created_at) }, relTime(e.created_at))),
          actRef,
          rows.length ? el("ul", { class: "cp-hist-changes" }, ...rows) : null));
    }

    const feed = el("ol", { class: "cp-hist-feed" });
    let busy = false, cursor = null, moreBtn = null;
    host.replaceChildren(feed);

    const qstr = (extra) => {
      const p = Object.assign({}, fixed, extra);
      return Object.keys(p).filter((k) => p[k] != null && p[k] !== false)
        .map((k) => k + "=" + encodeURIComponent(p[k])).join("&");
    };

    // older=false resets to the newest page (called on every open); older=true appends the
    // next page via the keyset cursor.
    async function load(older) {
      if (busy) return;
      busy = true;
      if (moreBtn) { moreBtn.remove(); moreBtn = null; }
      if (!older) { cursor = null; feed.replaceChildren(); }
      try {
        const q = qstr(older && cursor ? { before: cursor } : {});
        const j = await api("GET", baseUrl + (q ? "?" + q : ""));
        cursor = j.next_before;
        (j.entries || []).forEach((e) => feed.append(entryNode(e)));
        if (!feed.childElementCount) feed.append(el("li", { class: "cp-muted" }, emptyText));
        if (cursor) {
          moreBtn = el("button", { type: "button", class: "cp-mini" }, "Načíst starší");
          moreBtn.addEventListener("click", () => load(true));
          host.append(moreBtn);
        }
      } catch (e) {
        toast(e.message, true);
        if (!feed.childElementCount) feed.append(el("li", { class: "cp-muted" }, "Historii se nepodařilo načíst."));
      } finally {
        busy = false;
      }
    }

    return { reload: () => load(false) };
  };
})();
