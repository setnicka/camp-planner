// Lightweight client-side fuzzy matcher, shared by the activity picker and (later)
// the materials search — both filter a per-camp list in the browser.
//
// Diacritics are folded (so "fukc" matches "Fukční", "papir" matches "papír") and
// matching is order-preserving subsequence with bonuses for a contiguous hit and for
// matches at word starts. No dependency; deliberately simpler than rapidfuzz — good
// enough for short names/titles. Exposed as window.cpFuzzy = { fold, score, filter }.
"use strict";

(function () {
  const COMBINING = /[̀-ͯ]/g; // diacritical marks left by NFD decomposition

  function fold(s) {
    return String(s).normalize("NFD").replace(COMBINING, "").toLowerCase();
  }

  // Score `text` against `query`: higher is better, -1 means no match. Empty query
  // scores 0 (matches everything). A contiguous substring beats a scattered subsequence.
  function score(query, text) {
    const q = fold(query).trim();
    if (!q) return 0;
    const t = fold(text);
    const idx = t.indexOf(q);
    if (idx !== -1) return 1000 - idx * 2 - (t.length - q.length); // earlier + tighter = higher

    let ti = 0, total = 0, prev = -2, streak = 0;
    for (const c of q) {
      let found = -1;
      for (let k = ti; k < t.length; k++) if (t[k] === c) { found = k; break; }
      if (found === -1) return -1;                       // a query char missing → no match
      streak = found === prev + 1 ? streak + 1 : 0;
      let s = 1 + streak * 2;                             // reward runs of consecutive hits
      if (found === 0 || /[\s\-_.,/]/.test(t[found - 1])) s += 4; // and word-start hits
      total += s; prev = found; ti = found + 1;
    }
    return total;
  }

  // Return the matching items, best first. Empty query returns the list unchanged.
  function filter(query, list, keyFn) {
    if (!String(query).trim()) return list.slice();
    const scored = [];
    for (const item of list) {
      const sc = score(query, keyFn ? keyFn(item) : item);
      if (sc >= 0) scored.push({ item, sc });
    }
    scored.sort((a, b) => b.sc - a.sc);
    return scored.map((r) => r.item);
  }

  window.cpFuzzy = { fold, score, filter };
})();
