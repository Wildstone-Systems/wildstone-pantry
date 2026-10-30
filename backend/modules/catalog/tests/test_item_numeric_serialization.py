"""Item nutrition fields must serialise as JSON numbers, not strings.

The columns are Postgres Numeric, and ItemRead originally typed them as Decimal,
which Pydantic renders as a JSON string ("2.11"). The catalog edit modal binds
them to NumberInputs that only display numbers, so every scanned macro showed
blank — the data was saved and returned, just as the wrong JSON type. The
existing tests used Decimal(str(value)) and so were blind to string-vs-number.
"""

from __future__ import annotations

import httpx


NUMERIC_FIELDS = [
    "restock_threshold",
    "calories_per_unit",
    "energy_kcal_100g",
    "protein_g_100g",
    "fat_g_100g",
    "carbs_g_100g",
    "serving_size_g",
    "density_g_per_ml",
    "grams_per_each",
]


def test_item_numeric_fields_are_json_numbers(client: httpx.Client) -> None:
    created = client.post(
        "/api/items",
        json={
            "name": "Celsius",
            "energy_kcal_100g": 2.11,
            "protein_g_100g": 0,
            "restock_threshold": 3,
        },
    )
    assert created.status_code == 201, created.text
    item_id = created.json()["id"]

    for source, payload in (
        ("detail", client.get(f"/api/items/{item_id}").json()),
        ("list", next(i for i in client.get("/api/items").json()["items"] if i["id"] == item_id)),
    ):
        for field in NUMERIC_FIELDS:
            value = payload[field]
            assert value is None or isinstance(value, (int, float)), (
                f"{source}.{field} serialised as {type(value).__name__} ({value!r}); "
                "the frontend NumberInput needs a JSON number"
            )
        assert payload["energy_kcal_100g"] == 2.11
        assert payload["restock_threshold"] == 3
