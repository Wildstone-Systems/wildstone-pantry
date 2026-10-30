"""Recipe pictures: normalise on the way in, resolve one source on the way out.

Two things can supply a recipe's picture — an uploaded file and an upstream URL
carried in from an import — and exactly one place decides which wins
(``resolve_image_src``). Anything that renders a recipe asks that, so the
precedence can't drift apart between the list view, the detail view and the
cook screen.

Uploads are re-encoded rather than stored as received. A phone camera will
happily hand over a 12 MB 4000px JPEG for something that renders as a 300px
card, and storing that verbatim would put hundreds of megabytes of unviewed
pixels into the database and into every backup of it. Re-encoding also strips
EXIF — which matters twice over: it carries GPS coordinates from wherever the
photo was taken, and its orientation flag is the reason phone photos so often
appear rotated. Applying the rotation here means the stored bytes are already
the right way up for every consumer.
"""

from __future__ import annotations

import io
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Recipe
from app.models.recipe_image import RecipeImage

# Generous enough for a full-bleed header on a desktop display, small enough
# that a hundred recipes is tens of megabytes rather than gigabytes.
MAX_DIMENSION = 1400

# What we'll accept off the wire, before re-encoding. This is a guard against
# someone uploading a video or a disk image by accident, not a security
# boundary — the real check is that Pillow can decode it as an image at all.
MAX_UPLOAD_BYTES = 15 * 1024 * 1024

JPEG_QUALITY = 82

STORED_CONTENT_TYPE = "image/jpeg"


class ImageRejected(ValueError):
    """The upload isn't something we can store as a recipe picture."""


def _normalise(raw: bytes) -> tuple[bytes, int, int]:
    """Decode, rotate upright, shrink to fit, re-encode as JPEG."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    if not raw:
        raise ImageRejected("The uploaded file was empty.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ImageRejected(
            f"That image is {len(raw) // (1024 * 1024)} MB — the limit is "
            f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
        )

    try:
        img = Image.open(io.BytesIO(raw))
        # Apply the EXIF orientation flag and drop the metadata with it, so the
        # stored bytes are upright for everyone and carry no location data.
        img = ImageOps.exif_transpose(img)
        img.load()
    except UnidentifiedImageError as exc:
        raise ImageRejected("That file isn't an image we can read.") from exc
    except OSError as exc:  # truncated / malformed files land here
        raise ImageRejected("That image file appears to be damaged.") from exc

    # JPEG has no alpha channel; flatten transparency onto white rather than
    # letting the encoder fail or produce a black background.
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        from PIL import Image as _Image

        backdrop = _Image.new("RGB", img.size, (255, 255, 255))
        backdrop.paste(img, mask=img.split()[-1])
        img = backdrop
    elif img.mode != "RGB":
        img = img.convert("RGB")

    img.thumbnail((MAX_DIMENSION, MAX_DIMENSION))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return buf.getvalue(), img.width, img.height


def set_recipe_image(db: Session, recipe_id: UUID, raw: bytes) -> RecipeImage:
    """Store (or replace) a recipe's uploaded picture.

    Replacing overwrites in place — the primary key is the recipe id, so there
    is no way to accumulate orphaned rows for a recipe that has been re-shot
    several times.
    """
    recipe = db.get(Recipe, recipe_id)
    if recipe is None:
        raise LookupError("Recipe not found")

    data, width, height = _normalise(raw)

    row = db.get(RecipeImage, recipe_id)
    if row is None:
        row = RecipeImage(recipe_id=recipe_id)
        db.add(row)
    row.content_type = STORED_CONTENT_TYPE
    row.data = data
    row.width = width
    row.height = height
    row.byte_size = len(data)
    db.commit()
    db.refresh(row)
    return row


def get_recipe_image(db: Session, recipe_id: UUID) -> RecipeImage | None:
    return db.get(RecipeImage, recipe_id)


def delete_recipe_image(db: Session, recipe_id: UUID) -> bool:
    """Remove an uploaded picture. Any imported ``image_url`` is left alone, so
    deleting a photo you took falls back to the recipe's original picture rather
    than leaving it blank."""
    row = db.get(RecipeImage, recipe_id)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def recipes_with_uploads(db: Session, recipe_ids: list[UUID]) -> set[UUID]:
    """Which of these recipes have an uploaded picture.

    Batched on purpose: the list view needs this for every row, and asking
    per-recipe would be a query per card. Selects only the key — never the
    blob — so the cost is independent of how large the pictures are.
    """
    if not recipe_ids:
        return set()
    rows = db.execute(
        select(RecipeImage.recipe_id).where(RecipeImage.recipe_id.in_(recipe_ids))
    ).scalars()
    return set(rows)


def resolve_image_src(recipe: Recipe, *, has_upload: bool) -> str | None:
    """The one place picture precedence is decided.

    An uploaded photo beats an imported one: if you went to the trouble of
    photographing your own version of the dish, that is the picture you meant.
    """
    if has_upload:
        return f"/api/recipes/{recipe.id}/image"
    return recipe.image_url
