"""Recipe pictures — upload, precedence, and what gets rejected.

The interesting behaviour is the normalisation on the way in (a phone photo is
not what we want to store or hand back) and the precedence on the way out (two
sources can supply a picture, and only one place decides).
"""

from __future__ import annotations

import io
import uuid

import pytest


def _png(width: int = 40, height: int = 40, colour: str = "red") -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def recipe(client):
    return client.post(
        "/api/recipes", json={"name": f"Photo Test {uuid.uuid4().hex[:6]}", "servings": 2}
    ).json()


def test_a_recipe_starts_with_no_picture(client, recipe):
    detail = client.get(f"/api/recipes/{recipe['id']}").json()
    assert detail["recipe"]["image_src"] is None
    assert client.get(f"/api/recipes/{recipe['id']}/image").status_code == 404


def test_upload_then_fetch_the_picture(client, recipe):
    up = client.post(
        f"/api/recipes/{recipe['id']}/image",
        files={"file": ("dish.png", _png(), "image/png")},
    )
    assert up.status_code == 200, up.text
    assert up.json()["image_src"] == f"/api/recipes/{recipe['id']}/image"

    got = client.get(f"/api/recipes/{recipe['id']}/image")
    assert got.status_code == 200
    # Stored re-encoded as JPEG regardless of what was sent up.
    assert got.headers["content-type"] == "image/jpeg"
    assert len(got.content) > 0

    # And the detail view now points at it.
    detail = client.get(f"/api/recipes/{recipe['id']}").json()
    assert detail["recipe"]["image_src"] == f"/api/recipes/{recipe['id']}/image"


def test_oversized_photos_are_shrunk_not_stored_whole(client, recipe):
    """A phone hands over thousands of pixels for something rendered at 300.
    Storing that verbatim would bloat the database and every backup of it."""
    from PIL import Image

    from modules.planning.services.recipe_images import MAX_DIMENSION

    client.post(
        f"/api/recipes/{recipe['id']}/image",
        files={"file": ("huge.png", _png(3000, 2000), "image/png")},
    )
    stored = Image.open(io.BytesIO(client.get(f"/api/recipes/{recipe['id']}/image").content))
    assert max(stored.size) == MAX_DIMENSION
    # Aspect ratio preserved — no squashing.
    assert stored.width / stored.height == pytest.approx(3000 / 2000, rel=0.01)


def test_reuploading_replaces_rather_than_accumulates(client, recipe):
    from app.db import SessionLocal
    from app.models.recipe_image import RecipeImage

    for colour in ("red", "blue", "green"):
        client.post(
            f"/api/recipes/{recipe['id']}/image",
            files={"file": ("d.png", _png(colour=colour), "image/png")},
        )

    db = SessionLocal()
    try:
        assert db.query(RecipeImage).filter_by(recipe_id=uuid.UUID(recipe["id"])).count() == 1
    finally:
        db.close()


def test_a_non_image_is_refused_with_a_readable_reason(client, recipe):
    res = client.post(
        f"/api/recipes/{recipe['id']}/image",
        files={"file": ("notes.txt", b"this is not a picture", "text/plain")},
    )
    assert res.status_code == 400
    assert "image" in res.json()["detail"].lower()


def test_uploading_to_a_recipe_that_does_not_exist_is_a_404(client):
    res = client.post(
        f"/api/recipes/{uuid.uuid4()}/image",
        files={"file": ("dish.png", _png(), "image/png")},
    )
    assert res.status_code == 404


def test_an_imported_picture_is_kept_and_rendered(client):
    """TheMealDB has always returned a thumbnail; the import path used to drop it."""
    created = client.post(
        "/api/recipes",
        json={
            "name": f"Imported {uuid.uuid4().hex[:6]}",
            "servings": 4,
            "image_url": "https://example.test/dish.jpg",
        },
    ).json()
    assert created["image_src"] == "https://example.test/dish.jpg"


def test_your_own_photo_wins_over_the_imported_one(client):
    """If you went to the trouble of photographing your version, that's the one
    you meant."""
    created = client.post(
        "/api/recipes",
        json={
            "name": f"Both {uuid.uuid4().hex[:6]}",
            "servings": 4,
            "image_url": "https://example.test/stock.jpg",
        },
    ).json()

    client.post(
        f"/api/recipes/{created['id']}/image",
        files={"file": ("mine.png", _png(), "image/png")},
    )
    detail = client.get(f"/api/recipes/{created['id']}").json()
    assert detail["recipe"]["image_src"] == f"/api/recipes/{created['id']}/image"

    # Deleting your photo falls back to the imported one rather than going blank.
    assert client.delete(f"/api/recipes/{created['id']}/image").status_code == 204
    detail = client.get(f"/api/recipes/{created['id']}").json()
    assert detail["recipe"]["image_src"] == "https://example.test/stock.jpg"


def test_a_metadata_only_update_does_not_wipe_the_picture(client):
    created = client.post(
        "/api/recipes",
        json={
            "name": f"Keep {uuid.uuid4().hex[:6]}",
            "servings": 4,
            "image_url": "https://example.test/dish.jpg",
        },
    ).json()

    updated = client.put(
        f"/api/recipes/{created['id']}",
        json={"name": "Renamed", "servings": 6},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["image_src"] == "https://example.test/dish.jpg"


def test_the_list_view_carries_pictures_too(client, recipe):
    client.post(
        f"/api/recipes/{recipe['id']}/image",
        files={"file": ("dish.png", _png(), "image/png")},
    )
    rows = client.get("/api/recipes").json()
    row = next(r for r in rows if r["id"] == recipe["id"])
    assert row["image_src"] == f"/api/recipes/{recipe['id']}/image"


def test_deleting_a_recipe_takes_its_picture_with_it(client, recipe):
    from app.db import SessionLocal
    from app.models.recipe_image import RecipeImage

    client.post(
        f"/api/recipes/{recipe['id']}/image",
        files={"file": ("dish.png", _png(), "image/png")},
    )
    assert client.delete(f"/api/recipes/{recipe['id']}").status_code in (200, 204)

    db = SessionLocal()
    try:
        assert db.query(RecipeImage).filter_by(recipe_id=uuid.UUID(recipe["id"])).count() == 0
    finally:
        db.close()
