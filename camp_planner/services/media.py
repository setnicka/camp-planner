"""Photo files for the warehouse: resize on upload, serve from MEDIA_DIR.

Storage is a plain directory tree, no DB blobs:

    <MEDIA_DIR>/inventory/full/<uuid>.jpg    max 2048 px, what the lightbox shows
    <MEDIA_DIR>/inventory/thumb/<uuid>.jpg   max 320 px, the photo grids
    <MEDIA_DIR>/inventory/mini/<uuid>.jpg    max 96 px, the tiny per-row preview

The uploaded original is not kept. Only the InventoryPhoto row names a file, so
deleting one is the caller's job.

MEDIA_DIR unset = uploads refused and the photo UI hidden. Pillow comes with the
camp-planner[photos] extra and is imported lazily, so a deployment that stores no
photos needs no image library.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from camp_planner.extensions import state
from camp_planner.services import errors

# The stored sizes, largest first: store() shrinks each one out of the previous.
# mini is 96 px for 2 rem rows on a 3x phone display.
SIZES = (("full", 2048, 85), ("thumb", 320, 80), ("mini", 96, 75))
VARIANTS = tuple(variant for variant, _, _ in SIZES)
FULL_PX = SIZES[0][1]

# What a photo may be. Everything else Pillow reads stays out: EPS alone would hand the
# upload to Ghostscript.
_FORMATS = ("JPEG", "PNG", "WEBP", "GIF")   # JPEG covers the phones' MPO too
# Pixels one upload may decode to. Pillow's own bomb guard only warns below ~179 Mpx, and
# only a JPEG shrinks while decoding (draft), so a tiny PNG could claim gigabytes.
_MAX_PIXELS = 40_000_000

# Names we generate ourselves; anything else in a URL is a probe, not a photo.
_FILENAME = re.compile(r"^[0-9a-f]{32}\.jpg\Z")


def root() -> Path | None:
    """The configured media directory, or None when photos are switched off."""
    configured = state().get("media_dir")
    # Absolute: a relative path would diverge between writing (resolved against the
    # process cwd) and serving (send_from_directory resolves it against the package).
    return Path(configured).absolute() if configured else None


def enabled() -> bool:
    return root() is not None


def variant_dir(variant: str) -> Path:
    """Directory holding one size of the photos; callers have checked enabled(). Only
    store() creates it, a missing directory means no files."""
    return root() / "inventory" / variant


def valid_filename(filename: str) -> bool:
    return bool(_FILENAME.match(filename))


def store(stream: BinaryIO) -> str:
    """Save one uploaded image in every size, returning the generated filename.

    Raises errors.Invalid on anything Pillow cannot read, so a stray PDF or a truncated
    upload fails as a business error rather than a 500.
    """
    if not enabled():
        raise errors.Invalid("Nahrávání fotek není na tomto serveru zapnuté.")
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError as exc:
        raise errors.Invalid(
            "Server neumí zpracovat fotky: chybí knihovna Pillow "
            "(nainstalovat balíček camp-planner[photos])."
        ) from exc

    try:
        image = Image.open(stream, formats=_FORMATS)
        # Let libjpeg downscale while decoding.
        image.draft("RGB", (FULL_PX, FULL_PX))
        if image.width * image.height > _MAX_PIXELS:   # the size after draft, still undecoded
            raise errors.Invalid("Obrázek má příliš velké rozměry.")
        # Phones record orientation in EXIF instead of rotating the pixels.
        image = ImageOps.exif_transpose(image)
        # JPEG has no alpha channel, and a palette image would save as garbage.
        image = image.convert("RGB")
    except UnidentifiedImageError as exc:
        raise errors.Invalid("Soubor není obrázek, který bychom uměli zpracovat.") from exc
    # Pillow's decompression-bomb guard: a few MB of JPEG can claim hundreds of
    # megapixels. Its error descends from Exception alone, so it needs its own clause.
    except Image.DecompressionBombError as exc:
        raise errors.Invalid("Obrázek má příliš velké rozměry.") from exc
    except OSError as exc:
        raise errors.Invalid("Obrázek se nepodařilo přečíst (poškozený soubor?).") from exc

    filename = f"{uuid4().hex}.jpg"
    written: list[Path] = []
    try:
        for variant, px, quality in SIZES:
            directory = variant_dir(variant)
            directory.mkdir(parents=True, exist_ok=True)
            image.thumbnail((px, px))
            path = directory / filename
            image.save(path, "JPEG", quality=quality, optimize=True)
            written.append(path)
    except Exception:
        # The caller never learns a half-written set's name, so clean up here. Re-raised
        # as is: a failed write is a 500, not the uploader's fault.
        for path in written:
            path.unlink(missing_ok=True)
        raise
    return filename


def delete(filenames: list[str]) -> None:
    """Remove every size of each photo. Call only after the DB change committed: an
    orphaned file is harmless, a row pointing at a deleted file is not."""
    if not enabled():
        return
    for variant in VARIANTS:
        directory = variant_dir(variant)
        for filename in filenames:
            (directory / filename).unlink(missing_ok=True)
