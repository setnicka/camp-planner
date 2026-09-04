// Camp Planner: pieces shared by the warehouse (sklad) pages.
//
// What every page lists and does with a thing (a thing has no page of its own: its fields
// and photos live in one modal, moving, restoring and removing are buttons wherever it is
// listed), plus the check progress, the location grouping and search.
"use strict";

window.cpInventory = (function () {
  const { el, api, asInstant, amountText, czechKey, formModal, lightbox, openModal,
          searchPicker, submit, thumb: domThumb, toast, withId, actionGroup: domActionGroup } = window.cpDom;

  function amountEl(count, unit) {
    const text = amountText(count, unit);
    // The empty dash keeps the amount class, so it sits (and aligns) as an amount.
    return el("span", { class: "cp-inv-amount" + (text ? "" : " cp-dim") }, text || "—");
  }

  const recordUrl = (urls, boxId, itemId) => withId(urls.record, boxId, itemId);

  // A stored photo in one of its sizes: the server sends the file name, the route is
  // ours to fill in (the same sentinel template every other url uses).
  const photoUrl = (urls, photo, variant) => withId(urls.photo, variant, photo.filename);

  const boxUrl = (urls, id) => withId(urls.boxDetail, id);
  const checkUrl = (urls, id) => withId(urls.checkDetail, id);
  // A shelf on the overview map. The map is rendered client-side, so the overview reads
  // the hash itself (locationOf) rather than relying on the browser's fragment jump.
  const locationUrl = (urls, loc) => urls.overview + "#loc-" + encodeURIComponent(loc);
  const locationOf = (hash) =>
    hash.startsWith("#loc-") ? decodeURIComponent(hash.slice(5)) : null;

  const countLabel = (n, one, few, many) => n + " " + window.cpDom.plural(n, one, few, many);
  const things = (n) => countLabel(n, "věc", "věci", "věcí");

  const fmtDate = (iso) => asInstant(iso).toLocaleDateString("cs");
  // Column-head short: the year only once it stops being this one.
  const fmtDay = (iso) => {
    const d = asInstant(iso);
    return d.getFullYear() === new Date().getFullYear()
      ? d.toLocaleDateString("cs", { day: "numeric", month: "numeric" }) : fmtDate(iso);
  };

  // Only what happened: a row of zeros says nothing and, on a phone, costs a line.
  function impactText(s) {
    return [["jiný počet", s.adjusted], ["vyřazeno", s.discarded],
            ["přesunuto", s.moved], ["vráceno", s.revived]]
      .filter(([, n]) => n).map(([label, n]) => `${label} ${n}`).join(" · ");
  }
  const summaryText = (s) => {
    const impact = impactText(s);
    return `zkontrolováno ${s.checked}` + (impact ? " · " + impact : "");
  };

  // Where a record's item sat when the record was created. NULL = no box back then
  // (came back from discarded); an id no box has anymore = the box was deleted since
  // (the snapshot deliberately has no FK). nameOf(id) returns a name or undefined.
  function fromLabel(record, nameOf) {
    if (record.from_box_id === null) return "z vyřazených";
    const name = nameOf(record.from_box_id);
    return name ? "z krabice „" + name + "“" : "ze smazané krabice";
  }

  // Writes race, because the controls stay live: every response carries the whole fresh
  // state, so only the newest answer may be applied, an older one would undo what the
  // newer one did. The newest request is not always the last the server saw, though: when
  // a superseded answer is the last to land, or the newest write fails after one was
  // dropped, `refresh` (optional) fetches the state again.
  // Returns run(promise) for one such series, resolving false only when the write failed
  // (a superseded answer is no error), so a dialog can keep itself open on a failure.
  function latest(apply, refresh) {
    let seq = 0;
    let pending = 0;
    let dropped = false;   // a successful answer went unshown since the last apply
    return async (promise) => {
      const mine = ++seq;
      pending++;
      try {
        const answer = await promise;
        if (mine === seq) {
          apply(answer);
          dropped = false;
        } else {
          dropped = true;
          if (pending === 1 && refresh) refresh();
        }
        return true;
      } catch (e) {
        toast(e.message, true);
        if (mine === seq && dropped && refresh) refresh();
        return false;
      } finally {
        pending--;
      }
    };
  }

  // The progress chip, green and ticked once done.
  const progressEl = (done, total) => {
    const finished = total && done >= total;
    return el("span", { class: "cp-inv-progress" + (finished ? " cp-inv-done" : "") },
      (finished ? "✓ " : "") + `${done}/${total}`);
  };

  // Shelf codes in human order (A2 before A10), names in Czech order.
  const collator = new Intl.Collator("cs", { numeric: true, sensitivity: "base" });

  const sumProgress = (rows) => rows.reduce(
    (acc, r) => ({ checked: acc.checked + r.checked, total: acc.total + r.total }),
    { checked: 0, total: 0 });

  // `name` is text or, where the page is not the check's own, a link to it. `scope` says
  // what the numbers count when it is not the whole warehouse; it travels with them, so a
  // narrow screen never leaves it on a line of its own.
  function checkBar(name, checked, total, scope, ...extras) {
    return el("div", { class: "cp-inv-bar" },
      el("span", { class: "cp-inv-bar-name" }, "Probíhá: ", name),
      el("span", { class: "cp-inv-bar-count" },
        progressEl(checked, total),
        scope ? el("span", { class: "cp-muted" }, " " + scope) : null),
      ...extras);
  }

  // `locations` are the ones in use, offered as the field's suggestions: a typo there would
  // open a shelf of its own on the overview.
  function boxModal({ title, okLabel, box = {}, locations = [], onSubmit }) {
    const name = el("input", { type: "text", maxLength: 255, class: "cp-modal-name",
                               value: box.name || "" });
    const suggest = el("datalist", { id: "cp-inv-locations" },
      ...[...new Set(locations.filter(Boolean))].sort(collator.compare)
        .map((loc) => el("option", { value: loc })));
    const location_ = el("input", { type: "text", maxLength: 255, class: "cp-modal-name",
                                    value: box.location || "" });
    location_.setAttribute("list", suggest.id);   // a getter-only property, el() cannot set it
    const note = el("textarea", { rows: 3, class: "cp-act-textarea" }, box.note || "");
    const virtual = el("input", { type: "checkbox", checked: !!box.virtual });
    formModal({
      title,
      pane: el("div", { class: "cp-pane" },
        el("label", { class: "cp-field-label" }, "Název"), name,
        el("label", { class: "cp-field-label" }, "Umístění"), location_, suggest,
        el("div", { class: "cp-field-hint" }, "Kde krabice stojí, např. „sklep, police 2“."),
        el("label", { class: "cp-field-label" }, "Poznámka"), note,
        el("label", { class: "cp-inv-virtual" }, virtual,
          "Virtuální krabice – jen místo, kde věci leží volně"),
        el("div", { class: "cp-field-hint" },
          "V přehledu se kreslí čárkovaně přes celý řádek.")),
      okLabel,
      onSubmit: (close) => onSubmit({
        name: name.value.trim(),
        location: location_.value.trim() || null,
        note: note.value.trim() || null,
        virtual: virtual.checked,
      }, close),
    });
  }

  // The shared thumbnail, opening the thing's photos dialog rather than the bare lightbox.
  function thumb(item, urls) {
    return domThumb(urls.photo, item.photos.map((p) => p.filename),
                    { zoom: item.name, onOpen: () => photosModal(item, urls) });
  }

  // A small label on a row or a photo; `kids` is text or nodes. Placed by the caller, not
  // by itemLabel: where a row is about what a running check is doing to the thing, that is
  // what it should say, and "vyřazeno" next to it would only read as a contradiction.
  const badge = (kids, title) =>
    el("span", { class: "cp-inv-badge", ...(title ? { title } : {}) }, ...[].concat(kids));

  // Name + thumbnail, other names muted alongside (+ the "where to buy" link as a small ↗,
  // the materials pattern). `extra` are the caller's badges, laid out with the words.
  function itemLabel(item, urls, ...extra) {
    const name = el("span", null, item.name);
    // The link is the thing's, so it hangs on the name, glued with a non-breaking space:
    // as a flex item of its own it wraps away and ends up on a line by itself.
    if (item.url) {
      name.append(el("a", { class: "cp-inv-url", href: item.url, target: "_blank",
                            rel: "noopener", title: item.url }, "\u00a0↗"));
    }
    const text = el("span", { class: "cp-inv-item-text" },
      name,
      // Parenthesised, so wrapped onto a line of their own they still read as an aside.
      item.alt_names.length
        ? el("span", { class: "cp-inv-alt cp-muted" }, "(" + item.alt_names.join(", ") + ")")
        : null,
      ...extra);
    return el("span", { class: "cp-inv-item-name" }, thumb(item, urls), text);
  }

  // Boxes by where they stand, as a walk through the warehouse reads them: shelf codes in
  // human order (A2 before A10), locations in Czech order, boxes standing nowhere last,
  // and within a location the virtual "rest of the shelf" after the real boxes. Takes
  // entries carrying `.box` (a deleted one's is null and stands nowhere); returns
  // [location, entries] pairs.
  function groupByLocation(entries) {
    const byLocation = new Map();
    for (const entry of entries) {
      const key = (entry.box?.location || "").trim();
      if (!byLocation.has(key)) byLocation.set(key, []);
      byLocation.get(key).push(entry);
    }
    for (const group of byLocation.values()) {
      group.sort((a, b) => (a.box?.virtual ? 1 : 0) - (b.box?.virtual ? 1 : 0));
    }
    return [...byLocation.entries()].sort((a, b) => {
      if (!a[0] || !b[0]) return a[0] ? -1 : 1;
      return collator.compare(a[0], b[0]);
    });
  }

  // One section per location of groupByLocation's pairs, `body(entries)` filling it and
  // `head(entries, loc)` (optional) following the heading's name. A lone nameless group
  // carries no heading, and the boxes standing nowhere get a name.
  function locationSections(located, body, { head, attrs } = {}) {
    const heads = located.length > 1 || (located.length === 1 && !!located[0][0]);
    return located.map(([loc, entries]) => el("section", attrs?.(loc),
      heads ? el("h2", { class: "cp-inv-loc" }, loc || "Bez umístění",
                 ...(head ? [" ", head(entries, loc)] : [])) : null,
      body(entries)));
  }

  // The search field above a list. The magnifier is a pseudo-element of the wrap, so the
  // two are built together.
  function searchField({ placeholder, ariaLabel, onInput }) {
    const input = el("input", { type: "search", class: "cp-inv-search",
                                placeholder, "aria-label": ariaLabel });
    input.addEventListener("input", () => onInput(input.value));
    return { input, wrap: el("span", { class: "cp-inv-search-wrap" }, input) };
  }

  // "This element is the button": a click anywhere but an interactive child, or
  // Enter/Space while the element itself has the focus. Sets the role and the tab stop
  // too, so the keyboard affordance cannot be forgotten at a call site.
  function activatable(node, run, ignore = "a, button") {
    node.setAttribute("role", "button");
    node.tabIndex = 0;
    node.addEventListener("click", (e) => { if (!e.target.closest(ignore)) run(); });
    node.addEventListener("keydown", (e) => {
      if ((e.key === "Enter" || e.key === " ") && e.target === node) {
        e.preventDefault();
        run();
      }
    });
  }

  // A record grouped under a box it did not start in was moved there by its check. One
  // whose box was deleted since (box_id NULL) cannot tell any more.
  const moved = (record) => record.box_id !== null && record.from_box_id !== record.box_id;
  // Box names by id, for the pages that resolve a record's box (see fromLabel).
  const boxNameOf = (boxes) => {
    const names = new Map(boxes.map((b) => [b.id, b.name]));
    return (id) => names.get(id);
  };
  // A box named and linked, for the badges pointing at one; a box deleted since has no name.
  const boxLink = (urls, id, nameOf) =>
    el("a", { href: boxUrl(urls, id) }, nameOf(id) || "jiné krabice");

  function itemSubline(item) {
    return item.note ? el("div", { class: "cp-inv-row-sub cp-muted" }, item.note) : null;
  }

  // What the running check says about one item, seen from box `boxId`:
  //   "unchecked"   nobody has looked at it
  //   "here"        observed where it is filed
  //   "discarded"   observed as gone
  //   "moved-away"  filed here, but observed in another box
  //   "moved-in"    filed elsewhere (or retired), observed here
  function recordState(item, record, boxId) {
    if (!record) return "unchecked";
    if (record.discarded) return "discarded";
    if (record.box_id !== boxId) return "moved-away";
    if (item.box_id !== boxId) return "moved-in";
    return "here";
  }

  // What a thing matches on. The extras are the caller's context: the box name on the
  // overview map, the thing's own note inside a box.
  function searchText(item, ...extras) {
    return czechKey([item.name, ...item.alt_names, ...extras.filter(Boolean)].join(" "));
  }

  // Count + unit fields, shared by "jiný počet" and the item modal. Returns the pane and
  // a read() giving the values, with an empty count meaning "we have it, uncounted".
  function amountFields(count, unit, lock) {
    // inputmode: a phone opens the number pad, not the full keyboard.
    const countInput = el("input", { type: "number", step: "any", class: "cp-num", inputMode: "decimal",
                                     value: count == null ? "" : count });
    const unitInput = el("input", { type: "text", maxLength: 40, class: "cp-need-unit",
                                    value: unit || "", placeholder: "ks" });
    if (lock) countInput.disabled = unitInput.disabled = true;
    const pane = el("div", null,
      el("div", { class: "cp-inv-amount-row" },
        el("div", { class: "cp-inv-field" },
          el("label", { class: "cp-field-label" }, "Počet"), countInput),
        el("div", { class: "cp-inv-field" },
          el("label", { class: "cp-field-label" }, "Jednotka"), unitInput)),
      lock ? el("div", { class: "cp-field-hint" },
        "Probíhá inventura: počet se mění tlačítkem inventury „jiný počet“.") : null);
    // A typo ("2e") reads as an empty value, which would save "uncounted": refuse it, the
    // dialog stays open (formModal) with the number still in it.
    const read = () => {
      if (countInput.validity.badInput) {
        countInput.focus();
        throw new Error("Počet není číslo.");
      }
      return {
        count: countInput.value.trim() === "" ? null : Number(countInput.value),
        unit: unitInput.value.trim() === "" ? null : unitInput.value.trim(),
      };
    };
    // Focus with the value selected: typing replaces it, Enter saves (formModal).
    return { pane, read, focus: () => { countInput.focus(); countInput.select(); } };
  }

  // The shared segmented group (cpDom.actionGroup); `kind` colours a check row's active
  // verdict by what it says (ok/adjust/gone/off/move) and places it in the phone layout.
  const actionGroup = (defs, ...extras) =>
    domActionGroup(defs.map((d) => d && d.kind ? { ...d, cls: "cp-inv-seg-" + d.kind } : d), ...extras);

  // Just the photos of one thing: the grid, each tile opening the lightbox. What the
  // tiny per-row preview expands into (the fields live in the Upravit modal).
  // A viewer without a footer, so it closes by the ✕ in its head: on a phone the backdrop
  // around a wide dialog is a 16px strip, no target to count on.
  function photosModal(item, urls) {
    // No label over the grid: the dialog is titled with the thing's name already.
    const photos = photoSection(() => item, { urls, mayEdit: false, onItem: () => {} });
    photos.paint();
    const shut = el("button", { type: "button", class: "cp-modal-close", "aria-label": "Zavřít",
                                title: "Zavřít" }, "✕");
    const close = openModal(el("div", { class: "cp-modal cp-modal-wide" },
      el("div", { class: "cp-modal-head cp-modal-head-x" }, item.name, shut),
      el("div", { class: "cp-pane" }, itemSubline(item), photos.el)));
    shut.addEventListener("click", () => close());
  }

  // --- the item modal ---------------------------------------------------------

  // Downscale in the browser before sending: the server caps at MAX_PX anyway, so a
  // multi-MB phone photo shrinks to a fraction before it hits a slow uplink. A transfer
  // optimization only: on any failure (or no gain) the original goes up and the server
  // re-encodes either way, so drifting from media.FULL_PX costs bandwidth, not correctness.
  const MAX_PX = 2048;
  async function shrink(file) {
    let bitmap = null;
    try {
      bitmap = await createImageBitmap(file);   // applies EXIF orientation
      const scale = Math.min(1, MAX_PX / Math.max(bitmap.width, bitmap.height));
      if (scale === 1) return file;
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(bitmap.width * scale);
      canvas.height = Math.round(bitmap.height * scale);
      canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise((r) => canvas.toBlob(r, "image/jpeg", 0.85));
      canvas.width = canvas.height = 0;   // release the backing store now, not at GC
      return blob && blob.size < file.size
        ? new File([blob], file.name, { type: "image/jpeg" }) : file;
    } catch {
      return file;
    } finally {
      // A decoded bitmap is width × height × 4 bytes; a phone gallery pick of a dozen
      // would exhaust the tab's memory if these lingered until GC.
      if (bitmap) bitmap.close();
    }
  }

  // The photo part of the modal: grid + upload. Mutations report the fresh item through
  // onItem, and the grid repaints itself from it. `label` heads the grid where the dialog
  // is about more than the photos.
  function photoSection(getItem, { urls, mayEdit, onItem, label }) {
    const grid = el("div", { class: "cp-inv-photo-grid" });
    // In the edit modal the section sits under a rule: unlike the fields above it,
    // photo changes do not wait for "Uložit".
    const wrap = el("div", { class: "cp-inv-photos" + (mayEdit ? " cp-inv-photos-edit" : "") },
      label ? el("div", { class: "cp-field-label" }, label) : null, grid);

    const applyItem = latest((answer) => { onItem(answer.item); paint(); });

    // An upload and a delete answer with whole-item snapshots that are not supersets of
    // each other, so overlapping them lets the later answer drop the earlier one's
    // result. The dropzone already blocks itself while uploading; the buttons follow.
    let busy = false;

    const action = (method, tpl, id) => {
      if (busy) {
        toast("Fotky se ještě nahrávají.", true);
        return;
      }
      applyItem(api(method, withId(tpl, id)));
    };

    // The server keeps photos title-first, but while the dialog is open the grid holds
    // the order it was opened with: promoting just moves the badge, nothing jumps.
    const shownOrder = [];

    function paint() {
      const item = getItem();
      const photos = item.photos;
      const titleId = photos.length ? photos[0].id : null;
      for (const p of photos) if (!shownOrder.includes(p.id)) shownOrder.push(p.id);
      const byId = new Map(photos.map((p) => [p.id, p]));
      const shown = shownOrder.map((id) => byId.get(id)).filter(Boolean);
      const tiles = shown.map((photo, index) => el("div", { class: "cp-inv-photo" },
        // A real button, so the lightbox opens from the keyboard too.
        el("button", { type: "button", class: "cp-inv-photo-zoom",
                       "aria-label": "Zvětšit fotku",
                       onclick: () => lightbox(
                         shown.map((p) => photoUrl(urls, p, "full")), index, item.name) },
          el("img", { src: photoUrl(urls, photo, "thumb"), alt: "", loading: "lazy" })),
        photo.id === titleId ? badge("titulní") : null,
        mayEdit && photo.id !== titleId ? el("button",
          { type: "button", class: "cp-inv-photo-btn cp-inv-photo-star",
            title: "Nastavit jako titulní", "aria-label": "Nastavit jako titulní",
            onclick: () => action("POST", urls.photoTitle, photo.id) }, "★") : null,
        mayEdit ? el("button",
          { type: "button", class: "cp-inv-photo-btn cp-inv-photo-del",
            title: "Smazat fotku", "aria-label": "Smazat fotku",
            onclick: () => { if (window.confirm("Smazat fotku?")) action("DELETE", urls.photoItem, photo.id); } },
          "✕") : null));
      grid.replaceChildren(...(tiles.length ? tiles
        : [el("p", { class: "cp-muted" }, "Zatím bez fotek.")]));
    }

    if (mayEdit) {
      // The visually hidden file input still does the picking (on a phone it is what
      // offers the camera); the label around it is a dropzone, so files land by click
      // or by drag&drop alike.
      const input = el("input", { type: "file", accept: "image/*", multiple: true,
                                  class: "cp-inv-drop-input" });
      const zone = el("label", { class: "cp-inv-drop" },
        input, el("span", null, "Přidat fotky – kliknutím nebo přetažením"));

      const post = (form) => api("POST", withId(urls.itemPhotos, getItem().id), form);

      async function upload(files) {
        if (busy) {
          toast("Předchozí fotky se ještě nahrávají.", true);
          return;
        }
        // Files the browser could not type at all (HEIC on some systems) go up anyway;
        // Pillow decides, and says so cleanly. Only a known non-image is refused here.
        const picked = [...files].filter((f) => !f.type || f.type.startsWith("image/"));
        if (!picked.length) {
          toast(files.length ? "Tohle nevypadá jako fotka."
                             : "Přetáhnout jde soubor z počítače, ne obrázek z jiné stránky.",
                true);
          return;
        }
        busy = true;
        input.disabled = true;
        zone.classList.add("cp-inv-drop-busy");
        try {
          const form = new FormData();
          // One at a time: a batch decoded in parallel holds every bitmap at once.
          for (const file of picked) form.append("photos", await shrink(file));
          await applyItem(post(form));
        } finally {
          busy = false;
          input.disabled = false;
          zone.classList.remove("cp-inv-drop-busy");
        }
      }

      input.addEventListener("change", () => {
        const files = [...input.files];
        input.value = "";   // so re-picking the same file fires change again
        upload(files);
      });
      // Photos upload on the spot, so picking one is not unsaved form state: keep the
      // event out of the enclosing formModal, which would then ask before closing.
      input.addEventListener("input", (e) => e.stopPropagation());
      const over = (on) => zone.classList.toggle("cp-inv-drop-over", on);
      zone.addEventListener("dragover", (e) => { e.preventDefault(); over(true); });
      zone.addEventListener("dragleave", () => over(false));
      zone.addEventListener("drop", (e) => {
        e.preventDefault();
        over(false);
        upload(e.dataTransfer.files);
      });
      wrap.append(zone);
    }
    return { el: wrap, paint };
  }

  // A thing's fields for "Upravit" and "Přidat novou věc"; the after* slots take the
  // caller's hints.
  function itemFields(item = {}, { lock, afterName, afterAmount, afterNote } = {}) {
    const name = el("input", { type: "text", maxLength: 255, class: "cp-modal-name",
                               value: item.name || "" });
    const alt = el("input", { type: "text", class: "cp-modal-name",
                              value: (item.alt_names || []).join(", "),
                              placeholder: "oddělené čárkou" });
    const amounts = amountFields(item.count, item.unit, lock);
    const url = el("input", { type: "url", maxLength: 1024, class: "cp-modal-name",
                              value: item.url || "" });
    const note = el("textarea", { rows: 3, class: "cp-act-textarea" }, item.note || "");
    const nodes = [
      el("label", { class: "cp-field-label" }, "Název"), name, afterName || null,
      el("label", { class: "cp-field-label" }, "Další názvy (pomáhají jen při hledání)"), alt,
      amounts.pane, afterAmount || null,
      el("label", { class: "cp-field-label" }, "Odkaz"), url,
      el("label", { class: "cp-field-label" }, "Poznámka"), note, afterNote || null,
    ];
    // Locked amounts stay out of the request entirely: sending the stale values read at
    // modal-open time could overwrite what a completed check just wrote.
    const read = () => ({
      name: name.value.trim(),
      alt_names: alt.value.split(",").map((s) => s.trim()).filter(Boolean),
      url: url.value.trim() || null,
      note: note.value.trim() || null,
      ...(lock ? {} : amounts.read()),
    });
    return { nodes, read, name };
  }

  // A thing's fields and photos; read-only without mayEdit. onChange(item) fires per
  // mutation.
  function itemModal({ item, boxes, urls, mayEdit, photosEnabled, lockAmounts, onChange }) {
    let current = { ...item };
    const changed = (fresh) => { current = fresh || current; onChange(fresh); };

    if (!mayEdit) {
      const boxName = (boxes.find((b) => b.id === current.box_id) || {}).name;
      const photos = photosEnabled
        ? photoSection(() => current, { urls, mayEdit: false, onItem: changed, label: "Fotky" })
        : null;
      if (photos) photos.paint();
      const closeBtn = el("button", { type: "button", class: "cp-cancel" }, "Zavřít");
      const close = openModal(el("div", { class: "cp-modal cp-modal-wide" },
        el("div", { class: "cp-modal-head" }, current.name),
        el("div", { class: "cp-pane" },
          el("p", { class: "cp-inv-meta" },
            current.discarded_at ? badge("vyřazeno") : el("span", null, boxName || ""),
            " · ", amountEl(current.count, current.unit)),
          current.alt_names.length
            ? el("p", { class: "cp-muted" }, "Také: " + current.alt_names.join(", ")) : null,
          current.url ? el("p", null, el("a", { href: current.url, target: "_blank",
                                               rel: "noopener" }, current.url)) : null,
          current.note ? el("p", { class: "cp-inv-note-text" }, current.note) : null,
          photos ? photos.el : null),
        el("div", { class: "cp-modal-foot" }, closeBtn)));
      closeBtn.addEventListener("click", () => close());
      return;
    }

    const fields = itemFields(current, { lock: lockAmounts });
    const photos = photosEnabled
      ? photoSection(() => current, { urls, mayEdit: true, onItem: changed,
                                      label: "Fotky (ukládají se hned při změně)" })
      : null;
    if (photos) photos.paint();

    formModal({
      title: current.name,
      pane: el("div", { class: "cp-pane" }, ...fields.nodes, photos ? photos.el : null),
      onSubmit: async (close) => {
        changed((await api("PATCH", withId(urls.itemItem, current.id), fields.read())).item);
        close();
      },
    });
  }

  // Every "which box?" dialog: the name, the location as its hint.
  function boxPicker({ title, hint, boxes, onPick }) {
    searchPicker({ title, hint, items: boxes,
                   labelOf: (b) => b.name, metaOf: (b) => b.location || "", onPick });
  }

  // A pick that writes and gets the fresh item back. The row leaves the list it was in,
  // so a toast says where it went.
  const writesItem = (request, onChange, done) => async (box, close) => {
    try {
      const fresh = (await request(box)).item;
      close();
      onChange(fresh);
      toast(done(box));
    } catch (e) { toast(e.message, true); }
  };

  // Move an item for real (the master row, not an observation). Offered outside a check
  // only; during one a move is an observation on the row.
  function movePicker({ item, boxes, urls, onChange }) {
    boxPicker({
      title: "Přesunout – " + item.name,
      hint: "Do které krabice věc patří.",
      boxes: boxes.filter((b) => b.id !== item.box_id),
      onPick: writesItem(
        (box) => api("PATCH", withId(urls.itemItem, item.id), { box_id: box.id }), onChange,
        (box) => `Přesunuto do „${box.name}“.`),
    });
  }

  function restorePicker({ item, boxes, urls, onChange }) {
    boxPicker({
      title: "Vrátit do skladu – " + item.name,
      hint: "Do které krabice se věc vrací.",
      boxes,
      onPick: writesItem(
        (box) => api("POST", withId(urls.itemRestore, item.id), { box_id: box.id }), onChange,
        (box) => `Vráceno do „${box.name}“.`),
    });
  }

  // onChange(null): the item is gone.
  async function eraseItem(item, urls, onChange) {
    if (!window.confirm(`Nenávratně smazat „${item.name}“ včetně fotek a celé historie `
        + "inventur?")) return;
    try {
      await api("DELETE", withId(urls.itemItem, item.id));
      onChange(null);
    } catch (e) { toast(e.message, true); }
  }

  // "Odebrat": one dialog offering the recommended retire (acts right away) and the
  // destructive erase (extra confirm). Retiring drops off the offer where it makes no
  // sense: during a check it is an observation made on the row, and a thing already
  // retired has nothing left to retire.
  function removeModal({ item, urls, checking, onChange }) {
    const retired = !!item.discarded_at;
    const discard = checking || retired ? null
      : el("button", { type: "button", class: "cp-primary" }, "Vyřadit");
    const erase = el("button", { type: "button", class: "cp-primary cp-danger-btn" },
      "Smazat natrvalo");
    const cancel = el("button", { type: "button", class: "cp-cancel" }, "Zrušit");
    const close = openModal(el("div", { class: "cp-modal" },
      el("div", { class: "cp-modal-head" },
        (checking || retired ? "Smazat – " : "Odebrat – ") + item.name),
      el("div", { class: "cp-pane" },
        el("p", null, retired
          ? "Věc je vyřazená: v krabicích už není, ale zůstává v tomto seznamu "
            + "i ve všech inventurách a jde ji vrátit do skladu."
          : checking
            ? "Během inventury se chybějící věc zapisuje tlačítkem „vyřadit“ na řádku."
            : "„Vyřadit“ znamená, že věc už nemáme. Zmizí z krabic, ale zůstane "
              + "v seznamu vyřazených i ve všech inventurách, a jde ji vrátit."),
        el("p", { class: "cp-danger-text" }, "„Smazat“ je nevratné: zmizí i fotky a celá "
          + "historie inventur. Jen na omylem založené věci.")),
      // The irreversible one stands apart on the left, away from where the primary sits.
      el("div", { class: "cp-modal-foot" }, erase, cancel, discard)));
    cancel.addEventListener("click", () => close());
    discard?.addEventListener("click", () => submit(discard, async () => {
      const fresh = (await api("POST", withId(urls.itemDiscard, item.id))).item;
      close();
      onChange(fresh);
      toast("Vyřazeno.");
    }));
    erase.addEventListener("click", async () => {
      await eraseItem(item, urls, (gone) => { close(); onChange(gone); });
    });
  }

  return { actionGroup, activatable, amountEl, amountFields, badge, boxLink, boxModal,
           boxNameOf, boxPicker, boxUrl, checkBar, checkUrl, countLabel, fmtDate, fmtDay,
           fromLabel, groupByLocation, impactText, itemFields, itemLabel, itemModal,
           itemSubline, latest, locationOf, locationSections, locationUrl, moved,
           movePicker, progressEl, recordState, recordUrl, removeModal, restorePicker,
           searchField, searchText, sumProgress, summaryText, things };
})();
