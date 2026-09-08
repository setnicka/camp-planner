// Camp Planner: the warehouse as the camp pages see it: how a thing is named, placed,
// pictured and picked. Both camp pages show the same link and offer the same list, so the
// rules live here once. Exposed as window.cpStock; load after dom.js.
"use strict";

window.cpStock = (function () {
  const { el, api, withId, thumb, czechKey } = window.cpDom;

  // The server's duplicate-detection key (Material.normalize_name): the shared fold, then
  // alphanumeric tokens sorted. "A4 papír" and "Papíry, A4" are one name, so the client
  // offers and titles what the server would accept.
  const normName = (s) => czechKey(s).split(/[^a-z0-9]+/).filter(Boolean).sort().join(" ");
  const sameName = (a, b) => normName(a) === normName(b);

  // A thing with no box is a retired one: the serializer drops the box on discard.
  const retired = (item) => item.discarded_at != null;
  const boxName = (item) => (retired(item) ? "Vyřazené" : item.box.name);

  // Where a thing stands: its box as a link onto the thing itself, or the retired shelf.
  // `materialName` (optional) adds the thing's own name where it differs from it.
  function place(urls, item, materialName) {
    if (!item) return null;
    const named = materialName !== undefined && !sameName(item.name, materialName);
    const title = named ? { title: "Ve skladu jako „" + item.name + "“" } : {};
    if (retired(item)) return el("span", { class: "cp-stock-place cp-muted", ...title }, boxName(item));
    return el("a", { class: "cp-stock-place", ...title,
                     href: withId(urls.inventoryBox, item.box.id) + "#item-" + item.id }, boxName(item));
  }

  // A thing's title photo, opening the lightbox over all of them. `item` may be missing,
  // so a row without a thing still lines its name up with the rest.
  const photo = (urls, item, opts) => thumb(urls.inventoryPhoto, (item?.photos || []).map((p) => p.filename),
                                            { zoom: item?.name, ...opts });

  // The live things, fetched once per page visit (the picker reopens without a round trip).
  let cache = null;
  const items = () => cache;
  const load = (urls) => (cache
    ? Promise.resolve(cache)
    : api("GET", urls.inventoryItems).then((j) => (cache = j.items || [])));

  // How a thing appears in a picker: its photo, its name, the box it stands in, and the
  // alternative names it also answers to. The photos hold a place only where the list has
  // any, so the names line up without an empty column.
  function rows(urls, things) {
    const placeholder = !!urls.inventoryPhoto && things.some((it) => it?.photos.length);
    return {
      labelOf: (it) => it.name,
      searchOf: (it) => [it.name, ...it.alt_names].join(" "),
      metaOf: (it) => (it ? boxName(it) : null),
      iconOf: (it) => photo(urls, it, { placeholder, inline: true }),
    };
  }

  return { normName, sameName, retired, boxName, place, photo, load, items, rows };
})();
