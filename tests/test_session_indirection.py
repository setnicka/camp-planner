"""Guard on the session indirection: every DB access in the package goes through
extensions.db_session, never db.session.

Breaking that convention fails nowhere else: db.session is the obvious thing to write
and works in every test and in standalone mode, and only an embedded deployment with an
injected session breaks. Hence a scan, like test_css_palette.py.
"""

from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "camp_planner"

# All route through db.session, so all bypass an injected one. The 404 helpers have
# equivalents in extensions.py; db.engine is the engine injected mode doesn't have.
FORBIDDEN = ("db.session", "db.engine", "db.get_or_404", "db.first_or_404")

# The one module allowed to name them: it defines the proxy resolving to db.session.
EXEMPT = {"extensions.py"}

# remove() is scoped_session's, which the proxy deliberately never is; the one caller
# is the standalone-only sync sidecar (cli.py).
ALLOWED = ("db.session.remove()",)


def test_no_module_reaches_past_the_session_proxy():
    offenders = []
    for path in sorted(PKG.rglob("*.py")):
        if path.name in EXEMPT:
            continue
        source = path.read_text(encoding="utf-8")
        for allowed in ALLOWED:
            source = source.replace(allowed, "")
        for spelling in FORBIDDEN:
            if spelling in source:
                line = next(i for i, text in enumerate(source.splitlines(), 1)
                            if spelling in text)
                offenders.append(f"{path.relative_to(PKG.parent)}:{line}: {spelling}")
    assert not offenders, (
        "use `from camp_planner.extensions import db_session` (and its get_or_404 / "
        "first_or_404, or db_session.get_bind() for the engine). The spellings below are "
        "bound to db.session and break the injected-session mode:\n  "
        + "\n  ".join(offenders))
