#!/usr/bin/env python
"""Re-shoot the screenshots embedded in docs/pruvodce.md.

Boots the app against a demo database (see `flask seed-demo`), logs in and captures
the set. Most shots are taken as the editor account — what an ordinary organiser
sees; only the admin-gated pages are taken as 'admin', so no screenshot advertises
a control a normal user does not have.

    uv pip install -e '.[docs]' && uv run playwright install chromium
    uv run flask --app wsgi seed-demo --out demo/demo.sqlite
    uv run python scripts/shoot_docs.py
"""

from __future__ import annotations

import argparse
import io
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from camp_planner.demo_data import (
    ADMIN_USER,
    EDITOR_USER,
    HERO_ACTIVITY,
    PASSWORD,
    PREV_CAMP_NAME,
    SLOTS_ACTIVITY,
    SLUG,
)
from camp_planner.models.activity import Activity
from camp_planner.models.camp import Camp

REPO = Path(__file__).resolve().parent.parent
# Viewport tall enough that all nine day-rows fit unscrolled.
CONTEXT = {"viewport": {"width": 1440, "height": 1000}, "device_scale_factor": 2,
           "locale": "cs-CZ"}

# WebP encoding runs off the browser thread — it costs several seconds per run and would
# otherwise sit between shots as dead time. Futures are drained at the end of capture().
_encoder = ThreadPoolExecutor(4)
_pending: list[Future] = []


def wait_for(url: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1):  # noqa: S310 — fixed localhost URL
                return
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    raise SystemExit(f"server did not come up at {url}")


def spawn_server(db: Path, port: int) -> subprocess.Popen:
    """Start the Flask server; the caller waits for /healthz once it needs it up."""
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{db.resolve()}",
        "AUTH_MODE": "standalone",
        "SECRET_KEY": "docs-screenshots",
        "FLASK_DEBUG": "0",
    }
    return subprocess.Popen(
        [sys.executable, "-m", "flask", "--app", "wsgi", "run", "--port", str(port)],
        cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def read_demo(db: Path) -> tuple[dict[str, int], bool]:
    """The activity ids the shots point at (they shift if the seed changes) and whether
    the camp is connected to a calendar. Goes through the ORM so table names (incl.
    DB_TABLE_PREFIX) come from the one place that owns them, the models."""
    engine = create_engine(f"sqlite:///{db.resolve()}")
    try:
        with Session(engine) as session:
            camp = session.scalar(select(Camp).filter_by(slug=SLUG))
            if camp is None:
                raise SystemExit(f"{db} holds no camp {SLUG!r} — run: "
                                 f"flask --app wsgi seed-demo --out {db}")
            connected = camp.google_calendar_id is not None
            rows = session.execute(
                select(Activity.title, Activity.id).where(Activity.camp_id == camp.id)
            ).tuples().all()
    finally:
        engine.dispose()
    return dict(rows), connected


def login(page, base: str, username: str) -> None:
    page.goto(f"{base}/auth/login")
    page.fill("input[name=username]", username)
    page.fill("input[name=password]", PASSWORD)
    page.click("button[type=submit], input[type=submit]")
    page.wait_for_load_state()
    # A rejected login re-renders the form; without this the run would yield a full set
    # of screenshots of the login page.
    if "/auth/login" in page.url:
        raise SystemExit(f"login as {username!r} failed — is this database seeded by seed-demo?")


def _write_webp(png: bytes, dest: Path) -> None:
    """Store Playwright's PNG bytes as lossless WebP — identical pixels, roughly a third of
    the size on flat UI screenshots. Playwright can only write PNG/JPEG itself, so the
    conversion happens here rather than leaving PNGs on disk to clean up. method=4: the
    slowest setting (6) costs ~60 s more per run for ~10 % of file size."""
    from PIL import Image

    Image.open(io.BytesIO(png)).save(dest, "WEBP", lossless=True, quality=100, method=4)


def shoot(page, out: Path, name: str, *, full: bool = True, selector: str | None = None) -> None:
    """Capture the page (or, with `selector`, just its first match) into out/name."""
    page.wait_for_timeout(400)          # let fonts settle and any fade finish
    png = (page.locator(selector).first.screenshot() if selector
           else page.screenshot(full_page=full))
    _pending.append(_encoder.submit(_write_webp, png, out / name))
    print(f"  {name}")


def shoot_google_review(page, camp: str, out: Path) -> None:
    """The pull-review shot. Only called when the camp is connected, so any failure here
    is a regression (or a calendar with nothing staged) and should stop the run."""
    page.goto(f"{camp}/detail")
    page.wait_for_selector('[data-tax-tab="google"]')
    page.click('[data-tax-tab="google"]')
    pull = page.get_by_role("button", name="Načíst změny z Google")
    pull.wait_for()                     # the tab body renders after an async status fetch
    pull.click()
    # Either the rendered change list or the explicit no-changes note — never the spinner.
    page.locator(".cp-google-review-title, .cp-google-review .cp-muted").first.wait_for(
        timeout=60000)
    if not page.locator(".cp-google-review-title").count():
        raise SystemExit("calendar pull found no divergences — restage them before shooting")
    shoot(page, out, "10-google-nacteni.webp", selector=".cp-google")


def capture(base: str, out: Path, ids: dict[str, int], google: bool) -> None:
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    camp = f"{base}/camps/{SLUG}"

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(**CONTEXT)
        page = ctx.new_page()

        login(page, base, EDITOR_USER)

        # 01 — timeline.
        page.goto(camp)
        page.wait_for_selector(".vis-timeline")
        shoot(page, out, "01-timeline.webp")

        # 03 — activity detail: prep/main/cleanup slots, Markdown, material, todos, tags, history.
        page.goto(f"{camp}/activities/{ids[HERO_ACTIVITY]}")
        page.wait_for_selector(".cp-act-title")
        page.wait_for_timeout(600)               # history feed arrives over the API
        shoot(page, out, "03-detail-aktivity.webp")

        # 12 — the same activity's change history, while the page (and its already-fetched
        # history feed) is still loaded.
        page.get_by_role("button", name="Historie změn").click()
        page.wait_for_selector(".cp-hist-entry", timeout=15000)
        shoot(page, out, "12-historie-zmen.webp")

        # 04 / 05 / 06 — the table pages.
        # Both list views run to thousands of pixels; the top screenful is what the guide
        # needs, so these two are viewport shots rather than full-page ones.
        page.goto(f"{camp}/activities")
        page.wait_for_selector("table")
        shoot(page, out, "04-seznam-her.webp", full=False)
        page.get_by_role("button", name="Chronologicky").click()
        page.wait_for_timeout(300)
        shoot(page, out, "04b-seznam-her-chronologicky.webp", full=False)

        page.goto(f"{camp}/materials")
        page.wait_for_selector("table")
        shoot(page, out, "05-material.webp")

        page.goto(f"{camp}/todos")
        page.wait_for_selector("table, .cp-todo")
        shoot(page, out, "06-ukoly.webp")

        # 07 — settings, on the taxonomy tab. The pane body is JS-rendered, so wait for
        # its table, not just the server-rendered tab bar.
        page.goto(f"{camp}/detail")
        page.wait_for_selector("[data-tax-body] table")
        shoot(page, out, "07-nastaveni.webp")

        # 08 — one activity, eight slots, each named by its topic (override_name).
        # Over three slots the list collapses behind a toggle; expand it so all eight show.
        page.goto(f"{camp}/activities/{ids[SLOTS_ACTIVITY]}")
        page.wait_for_selector(".cp-slotchip")
        page.locator(".cp-slot-toggle").click()
        shoot(page, out, "08-sloty-nazvy.webp", selector=".cp-act-bar")

        # 09 — the shared slot dialog (name + who staffs the block).
        page.locator(".cp-slot-edit").first.click()
        page.wait_for_selector(".cp-modal-overlay")
        shoot(page, out, "09-orgove-slotu.webp", selector=".cp-modal-overlay > *")
        page.keyboard.press("Escape")

        # 10 — inbound review. Needs a calendar that differs from the DB, i.e.
        # `seed-demo --calendar` plus a push plus manually staged divergences.
        if google:
            shoot_google_review(page, camp, out)
        else:
            print("  10-google-nacteni.webp skipped — demo seeded without --calendar")

        # 11 — the same timeline in the dark theme.
        page.goto(camp)
        page.wait_for_selector(".vis-timeline")
        page.click('[data-cp-theme-switch] button[data-theme="dark"]')
        shoot(page, out, "11-tmavy-rezim.webp")

        # 02 — creating a camp is admin-only. A fresh context, not a logout: it drops the
        # session cookie *and* the localStorage theme the dark-mode shot just set.
        ctx.close()
        page = browser.new_context(**CONTEXT).new_page()
        login(page, base, ADMIN_USER)
        page.goto(f"{base}/camps/new")
        page.wait_for_selector("form")
        # Pick last year's camp so the shot actually shows the copy-settings feature
        # rather than its empty default.
        page.select_option("select[name=copy_from]", label=PREV_CAMP_NAME)
        shoot(page, out, "02-nova-akce.webp")

        browser.close()

    for future in _pending:
        future.result()                 # surface encode errors before declaring success


def check_guide_refs(out: Path) -> None:
    """Warn (not fail — the Google shot is legitimately optional) when the shot set and
    the images pruvodce.md embeds drift apart."""
    guide = REPO / "docs/pruvodce.md"
    referenced = set(re.findall(r"screenshots/([\w.-]+\.webp)", guide.read_text()))
    present = {p.name for p in out.glob("*.webp")}
    for name in sorted(referenced - present):
        print(f"WARNING: {guide.name} embeds {name}, which is not in {out}")
    for name in sorted(present - referenced):
        print(f"WARNING: {out / name} is not referenced by {guide.name}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=REPO / "demo/demo.sqlite")
    ap.add_argument("--out", type=Path, default=REPO / "docs/screenshots")
    ap.add_argument("--port", type=int, default=5099)
    args = ap.parse_args()

    if not args.db.exists():
        raise SystemExit(f"{args.db} not found — run: flask --app wsgi seed-demo --out {args.db}")

    proc = spawn_server(args.db, args.port)
    base = f"http://127.0.0.1:{args.port}"
    try:
        ids, google = read_demo(args.db)         # overlaps the Flask boot
        wait_for(f"{base}/healthz")
        print(f"shooting {base} → {args.out}")
        capture(base, args.out, ids, google)
    finally:
        proc.terminate()
        proc.wait()
    check_guide_refs(args.out)


if __name__ == "__main__":
    main()
