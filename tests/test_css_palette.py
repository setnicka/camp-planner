"""Guards on the CSS design-token palette in static/css/content.css: no colour
literal outside the palette block, every token defined and used, and the two dark
blocks (media query vs. attribute — CSS can't OR them) kept identical."""

import re
from pathlib import Path

CSS_DIR = Path(__file__).resolve().parent.parent / "camp_planner" / "static" / "css"
PALETTE = CSS_DIR / "content.css"
END_MARKER = "/* === end palette"

# Layout tokens live in the palette block but carry no colour, so they need no
# dark counterpart.
NON_COLOUR = {"--cp-page-width", "--cp-page-pad"}

# Per-component state written at runtime by JS — deliberately absent from the palette
# block. (Element-keyed values like the materials hue --h don't use the --cp- prefix.)
RUNTIME_STATE = {
    "--cp-pill-n", "--cp-pill-pos",   # .cp-pill knob: positions + the active one (theme.js, dom.js)
}

# The three palette blocks, by the exact selector list each carries.
LIGHT = ':root,\n[data-cp-theme="light"] {'
DARK_AUTO = '[data-cp-theme="auto"],\n  :root:has([data-cp-theme="auto"]) {'
DARK_FORCED = '[data-cp-theme="dark"],\n:root:has([data-cp-theme="dark"]) {'

# color-scheme has to follow the theme, but only on an element carrying data-cp-theme:
# embedded, :root is the host's document (see content.css).
COLOR_SCHEME_RULES = (
    '[data-cp-theme="light"],\n[data-cp-theme="auto"] { color-scheme: light; }',
    '[data-cp-theme="dark"] { color-scheme: dark; }',
    '[data-cp-theme="auto"] { color-scheme: dark; }',
)

HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
# An rgb()/hsl()/… call composed from tokens is fine — materials.css builds a tag colour
# from the runtime hue --h — so only a call with no var() inside counts as a literal.
FUNC = re.compile(r"\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color-mix)\(")
# Named colours are only a literal in a declaration *value*: "white-space" is a property.
VALUE = re.compile(r":([^;{}]*)[;}]")
WORD = re.compile(r"[a-z]+")
NAMED = {
    "white", "black", "red", "blue", "green", "grey", "gray", "silver", "orange",
    "yellow", "purple", "navy", "teal", "pink", "brown", "gold", "beige", "cyan",
    "magenta", "maroon", "olive", "lime", "aqua", "fuchsia", "ivory", "khaki", "coral",
}
DECL = re.compile(r"(--cp-[a-z0-9-]+)\s*:\s*([^;]+);")
# A token is consumed either through var() or, from JS, via getPropertyValue() — the
# timeline's day/night overlay needs the latter because vis strips var() from an
# item's inline style (see timeline.js).
USE = re.compile(r"""(?:var\(\s*|getPropertyValue\(\s*["'])(--cp-[a-z0-9-]+)""")


def _uses(text):
    return set(USE.findall(text))


def _all_uses(exclude_palette=False):
    """Token uses across every stylesheet and page script. The palette block's own
    declarations aren't uses, so the unused-token check excludes it."""
    files = sorted(CSS_DIR.glob("*.css")) + sorted((CSS_DIR.parent / "js").glob("*.js"))
    return set().union(*(_uses(_split_palette()[1] if exclude_palette and f == PALETTE
                               else f.read_text(encoding="utf-8"))
                         for f in files))


def _split_palette():
    text = PALETTE.read_text(encoding="utf-8")
    assert END_MARKER in text, "content.css lost its end-of-palette marker"
    palette, rest = text.split(END_MARKER, 1)
    return palette, rest


def _body(palette, selector):
    """The text of one palette block, between its selector list and the closing brace."""
    i = palette.index(selector)
    body = palette[i + len(selector) :]
    return body[: body.index("}")]


def _block(palette, selector):
    """The declarations of one palette block, as an ordered list of (name, value)."""
    return DECL.findall(_body(palette, selector))


def _find_literals(text):
    """Colour literals in a stylesheet: a hex, an rgb()/hsl()/… call with nothing
    var()-derived inside it, or a named colour used in a declaration value."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)   # a hex named in prose is fine
    found = set(HEX.findall(text))
    for m in FUNC.finditer(text):
        depth, i = 1, m.end()
        while i < len(text) and depth:                  # walk to the matching paren
            depth += (text[i] == "(") - (text[i] == ")")
            i += 1
        call = text[m.start() : i]
        if "var(" not in call:
            found.add(call)
    for value in VALUE.findall(text):
        found |= {w for w in WORD.findall(value.lower()) if w in NAMED}
    return found


def test_no_colour_literals_outside_the_palette():
    """Every colour lives in the palette block; the rest of the CSS uses var()."""
    palette, rest = _split_palette()
    offenders = {}
    for css in sorted(CSS_DIR.glob("*.css")):
        text = rest if css == PALETTE else css.read_text(encoding="utf-8")
        found = _find_literals(text)
        if found:
            offenders[css.name] = sorted(found)
    assert not offenders, f"colour literals outside the palette: {offenders}"


def test_every_referenced_token_is_defined():
    """Catches typos and tokens left dangling by a rename/merge."""
    palette, _ = _split_palette()
    defined = {name for name, _ in DECL.findall(palette)}
    dangling = _all_uses() - defined - RUNTIME_STATE
    assert not dangling, f"undefined tokens referenced: {sorted(dangling)}"


def test_unused_tokens_are_not_carried():
    """The palette shouldn't accumulate tokens nothing consumes."""
    palette, _ = _split_palette()
    defined = {name for name, _ in _block(palette, LIGHT)} - NON_COLOUR
    unused = defined - _all_uses(exclude_palette=True)
    assert not unused, f"unused palette tokens: {sorted(unused)}"


def test_the_two_dark_blocks_are_identical():
    """The OS path and the host-forced path must not drift."""
    palette, _ = _split_palette()
    auto = _block(palette, DARK_AUTO)
    forced = _block(palette, DARK_FORCED)
    assert auto == forced, "the prefers-color-scheme and data-cp-theme dark blocks differ"


def test_dark_covers_every_colour_token():
    palette, _ = _split_palette()
    light = {name for name, _ in _block(palette, LIGHT)} - NON_COLOUR
    dark = {name for name, _ in _block(palette, DARK_FORCED)}
    assert not (light - dark), f"tokens with no dark value: {sorted(light - dark)}"
    assert not (dark - light), f"dark-only tokens: {sorted(dark - light)}"


# Blocks that wrap the timeline. Its day/night items are z-index:-1: a background on a
# non-stacking-context ancestor paints over them, silently losing the shading.
WRAPS_THE_TIMELINE = {"content.css": ".cp-embed {", "timeline.css": ".vis-timeline,"}


def test_a_background_over_the_day_night_items_isolates():
    """Either no background on these, or isolation to reorder the z-index:-1 items above it."""
    for name, selector in WRAPS_THE_TIMELINE.items():
        text = (CSS_DIR / name).read_text(encoding="utf-8")
        body = _body(text, selector)
        if "background" in body:
            assert "isolation: isolate" in body, (
                f"{name} {selector} paints a background but is not a stacking context, so it "
                "hides the timeline's z-index:-1 day/night items"
            )


def test_color_scheme_follows_the_theme_but_only_on_our_own_elements():
    """Pinned themes need their own form controls, but :root is the *host's* document in
    embedded mode — so color-scheme rides on data-cp-theme, never on a token block."""
    palette, _ = _split_palette()
    for selector in (LIGHT, DARK_AUTO, DARK_FORCED):
        assert "color-scheme" not in _body(palette, selector), (
            f"{selector} pins color-scheme; that reaches the host's :root in embedded mode"
        )
    for rule in COLOR_SCHEME_RULES:
        assert rule in palette, f"missing color-scheme rule: {rule!r}"
