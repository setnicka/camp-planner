// Camp Planner — in-place Markdown editor (CodeMirror 5 + a toolbar), built into `host`:
// grouped actions with active-mark tracking, preview toggle, Ctrl/Cmd shortcuts,
// window-bottom auto-fit, unsaved-changes guards. Save awaits onSave(content) and closes;
// any close calls onClose(). Returns { cm, fit } so a tabbed host can refit when re-shown.
// Exposed as window.cpMarkdownEdit; load after dom.js; needs CodeMirror + markdown-it.
"use strict";

window.cpMarkdownEdit = function ({ host: pane, value, md, onSave, onClose }) {
  const { el, toast } = window.cpDom;
  const original = value || "";
  const barEl = el("div", { class: "cp-mde-bar" });
  const host = el("div", { class: "cp-mde" });
  const preview = el("div", { class: "cp-mde cp-mde-preview cp-markdown" });   // rendered view (toggle)
  preview.hidden = true;
  const save = el("button", { type: "button", class: "cp-primary" }, "Uložit");
  const cancel = el("button", { type: "button", class: "cp-cancel" }, "Zrušit");
  pane.replaceChildren(barEl, host, preview);

  const cm = CodeMirror(host, {
    value: original, mode: "markdown", lineWrapping: true,
    indentUnit: 2, tabSize: 2, indentWithTabs: false,
    extraKeys: {
      Tab: "indentMore",          // indent the line(s) by 2 spaces → arbitrary list nesting
      "Shift-Tab": "indentLess",
      Enter: "newlineAndIndentContinueMarkdownList",   // auto-continue lists (continuelist addon)
    },
  });

  // toolbar: wrap the selection (bold/italic/code) or toggle a line prefix (heading/quote/list)
  const wrap = (mark) => {
    const sel = cm.getSelection();
    if (sel) { cm.replaceSelection(mark + sel + mark); }
    else { const c = cm.getCursor(); cm.replaceSelection(mark + mark); cm.setCursor({ line: c.line, ch: c.ch + mark.length }); }
    cm.focus();
  };
  const eachSelectedLine = (fn) => {
    const from = cm.getCursor("from").line, to = cm.getCursor("to").line;
    for (let l = from, i = 0; l <= to; l++, i++) {
      const text = cm.getLine(l);
      cm.replaceRange(fn(text, i), { line: l, ch: 0 }, { line: l, ch: text.length });
    }
    cm.focus();
  };
  const togglePrefix = (pfx) => {
    const from = cm.getCursor("from").line, to = cm.getCursor("to").line;
    let allHave = true;
    for (let l = from; l <= to; l++) if (!cm.getLine(l).startsWith(pfx)) { allHave = false; break; }
    eachSelectedLine((t) => (allHave ? t.slice(pfx.length) : pfx + t));
  };
  const link = () => { cm.replaceSelection("[" + (cm.getSelection() || "odkaz") + "](url)"); cm.focus(); };
  const image = () => { cm.replaceSelection("![" + (cm.getSelection() || "popis") + "](url)"); cm.focus(); };
  const codeBlock = () => { cm.replaceSelection("```\n" + cm.getSelection() + "\n```"); cm.focus(); };
  const hr = () => { cm.replaceSelection("\n---\n"); cm.focus(); };
  // set the selected lines to heading `level` (1–3); clicking the current level removes it
  const setHeading = (level) => {
    const pfx = "#".repeat(level) + " ";
    eachSelectedLine((t) => {
      const body = t.replace(/^#{1,6}\s+/, "");        // strip any existing heading marker
      return t.startsWith(pfx) ? body : pfx + body;    // same level → toggle off; else (re)apply
    });
  };

  // inline SVG icons (Lucide, MIT), grouped with separators like a modern editor
  const svg = (p) => '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor"' +
    ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + p + "</svg>";
  const ICON = {
    bold: '<path d="M14 12a4 4 0 0 0 0-8H6v8"/><path d="M15 20a4 4 0 0 0 0-8H6v8Z"/>',
    italic: '<line x1="19" x2="10" y1="4" y2="4"/><line x1="14" x2="5" y1="20" y2="20"/><line x1="15" x2="9" y1="4" y2="20"/>',
    strikethrough: '<path d="M16 4H9a3 3 0 0 0-2.83 4"/><path d="M14 12a4 4 0 0 1 0 8H6"/><line x1="4" x2="20" y1="12" y2="12"/>',
    heading1: '<path d="M4 12h8"/><path d="M4 18V6"/><path d="M12 18V6"/><path d="m17 12 3-2v8"/>',
    heading2: '<path d="M4 12h8"/><path d="M4 18V6"/><path d="M12 18V6"/><path d="M21 18h-4c0-4 4-3 4-6 0-1.5-2-2.5-4-1"/>',
    heading3: '<path d="M4 12h8"/><path d="M4 18V6"/><path d="M12 18V6"/><path d="M17.5 10.5c1.7-1 3.5 0 3.5 1.5a2 2 0 0 1-2 2"/><path d="M17 17.5c2 1.5 4 .3 4-1.5a2 2 0 0 0-2-2"/>',
    quote: '<path d="M17 6H3"/><path d="M21 12H8"/><path d="M21 18H8"/><path d="M3 12v6"/>',
    list: '<path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/><path d="M3 6h.01"/><path d="M3 12h.01"/><path d="M3 18h.01"/>',
    ordered: '<path d="M11 6h10"/><path d="M11 12h10"/><path d="M11 18h10"/><path d="M4 6h1v4"/><path d="M4 10h2"/><path d="M6 18H4c0-1 2-2 2-3s-1-1.5-2-1"/>',
    code: '<path d="m16 18 6-6-6-6"/><path d="m8 6-6 6 6 6"/>',
    codeblock: '<path d="M10 9.5 8 12l2 2.5"/><path d="m14 9.5 2 2.5-2 2.5"/><rect width="18" height="18" x="3" y="3" rx="2"/>',
    link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    image: '<rect width="18" height="18" x="3" y="3" rx="2" ry="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>',
    hr: '<line x1="3" x2="21" y1="12" y2="12"/><polyline points="8 8 12 4 16 8"/><polyline points="16 16 12 20 8 16"/>',
    undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    redo: '<path d="m15 14 5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/>',
    preview: '<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>',
  };
  // preview toggle: swap editor for rendered markdown; formatting buttons disabled
  // while previewing, Save/Cancel stay live.
  let previewing = false;
  const togglePreview = () => {
    previewing = !previewing;
    if (previewing) {
      const src = cm.getValue();
      preview.innerHTML = src ? md.render(src) : '<p class="cp-muted">Bez popisu.</p>';
      preview.style.height = host.offsetHeight + "px";   // match the editor's current height
      host.hidden = true; preview.hidden = false;
    } else {
      preview.hidden = true; host.hidden = false;
      cm.refresh(); cm.focus();
    }
    for (const key in btnByKey) if (key !== "preview") btnByKey[key].disabled = previewing;
    btnByKey.preview.classList.toggle("active", previewing);
    if (!previewing) updateActive();   // restore undo/redo enabled-state + active marks
  };
  const GROUPS = [
    [["bold", "Tučně (Ctrl+B)", () => wrap("**")], ["italic", "Kurzíva (Ctrl+I)", () => wrap("*")], ["strikethrough", "Přeškrtnutí", () => wrap("~~")]],
    [["heading1", "Nadpis 1 (Ctrl+1)", () => setHeading(1)], ["heading2", "Nadpis 2 (Ctrl+2)", () => setHeading(2)], ["heading3", "Nadpis 3 (Ctrl+3)", () => setHeading(3)], ["quote", "Citace", () => togglePrefix("> ")]],
    [["list", "Odrážky", () => togglePrefix("- ")], ["ordered", "Číslovaný seznam", () => eachSelectedLine((t, i) => (i + 1) + ". " + t)]],
    [["code", "Kód", () => wrap("`")], ["codeblock", "Blok kódu", codeBlock], ["link", "Odkaz (Ctrl+K)", link], ["image", "Obrázek", image], ["hr", "Vodorovná čára", hr]],
    [["undo", "Zpět (Ctrl+Z)", () => cm.undo()], ["redo", "Vpřed (Ctrl+Y)", () => cm.redo()]],
    [["preview", "Náhled", togglePreview]],
  ];
  const btnByKey = {};
  GROUPS.forEach((group, gi) => {
    if (gi) barEl.append(el("span", { class: "cp-mde-sep" }));
    group.forEach(([key, title, action]) => {
      const btn = el("button", { type: "button", class: "cp-mde-btn", title });
      btn.innerHTML = svg(ICON[key]);   // static, trusted SVG markup
      btn.addEventListener("click", action);
      btnByKey[key] = btn;
      barEl.append(btn);
    });
  });
  barEl.append(el("div", { class: "cp-mde-actions" }, save, cancel));   // pushed to the right end
  // reflect the formatting under the caret on the toolbar (inline via token type, block via line)
  const updateActive = () => {
    const tok = cm.getTokenTypeAt(cm.getCursor()) || "";
    const line = cm.getLine(cm.getCursor().line) || "";
    const on = {
      bold: /strong/.test(tok), italic: /\bem\b/.test(tok), strikethrough: /strikethrough/.test(tok),
      code: /comment/.test(tok),
      heading1: /^#\s/.test(line), heading2: /^##\s/.test(line), heading3: /^###\s/.test(line),
      quote: /^\s*>/.test(line),
      list: /^\s*[-*+]\s/.test(line), ordered: /^\s*\d+[.)]\s/.test(line),
    };
    for (const key in btnByKey) btnByKey[key].classList.toggle("active", !!on[key]);
    const h = cm.historySize();   // grey out undo/redo when there's nothing to undo/redo
    btnByKey.undo.disabled = !h.undo;
    btnByKey.redo.disabled = !h.redo;
  };
  cm.on("cursorActivity", updateActive);
  cm.on("change", updateActive);   // history depth changes on edits (and on undo/redo itself)
  updateActive();
  // keyboard shortcuts (Ctrl on Win/Linux, Cmd on Mac); undo/redo are CM defaults already
  const keymap = {};
  const bind = (k, fn) => { keymap["Ctrl-" + k] = fn; keymap["Cmd-" + k] = fn; };
  bind("B", () => wrap("**")); bind("I", () => wrap("*")); bind("K", link);
  bind("1", () => setHeading(1)); bind("2", () => setHeading(2)); bind("3", () => setHeading(3));
  cm.addKeyMap(keymap);

  // grow the editor down to the bottom of the window; re-fit on resize (self-removing)
  const fit = () => {
    if (!host.isConnected) { window.removeEventListener("resize", fit); return; }
    if (host.offsetParent === null) return;   // hidden (another tab is active) — refit when shown
    cm.setSize(null, Math.max(240, window.innerHeight - host.getBoundingClientRect().top - 16));
  };
  fit();
  window.addEventListener("resize", fit);
  cm.focus();

  const dirty = () => cm.getValue() !== original;
  // Save is enabled only while there are unsaved changes
  const syncSave = () => { save.disabled = !dirty(); };
  cm.on("change", syncSave);
  syncSave();
  // warn on full page-leave (reload / navigate away / close) with unsaved changes
  const beforeUnload = (e) => { if (dirty()) { e.preventDefault(); e.returnValue = ""; } };
  window.addEventListener("beforeunload", beforeUnload);
  const close = () => {
    window.removeEventListener("resize", fit);
    window.removeEventListener("beforeunload", beforeUnload);
    onClose();
  };
  cancel.addEventListener("click", () => {
    if (dirty() && !confirm("Zahodit změny popisu?")) return;
    close();
  });
  save.addEventListener("click", async () => {
    if (!dirty()) return;                // disabled when unchanged; guard anyway
    save.disabled = true;
    try { await onSave(cm.getValue()); close(); }
    catch (e) { save.disabled = false; toast(e.message, true); }
  });

  return { cm, fit };
};
