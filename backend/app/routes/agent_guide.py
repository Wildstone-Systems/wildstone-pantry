"""Agent-facing API map (ungated).

A curated "which endpoint for which task" guide with concrete example payloads —
the thing raw OpenAPI doesn't give. Meant to be fetched by an agent before it
starts calling the API. Complements ``/openapi.json`` and ``/docs``.

Kept deliberately small and task-oriented. A test asserts every path listed here
resolves to a real route, so it can't silently drift from the implementation.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/agent", tags=["agent"])


# Each entry: a task an agent commonly needs, the call to make, and an example.
_TASKS: list[dict] = [
    {
        "task": "Create a recipe WITH ingredients (by name — items auto-created)",
        "method": "POST",
        "path": "/api/recipes",
        "body": {
            "name": "Spaghetti Bolognese",
            "instructions": "Brown the beef, add sauce, serve over pasta.",
            "servings": 6,
            "ingredients": [
                {"name": "ground beef", "quantity": 1, "unit": "lb"},
                {"name": "spaghetti", "quantity": 1, "unit": "lb"},
            ],
            "create_missing_items": True,
        },
        "notes": "Ingredients accept `name` OR `item_id`, plus `quantity` and `unit`. "
        "Unknown fields are rejected (422). Omit `ingredients` to leave them untouched. "
        "`name` must be the ingredient ALONE — 'avocado', never '2 avocados' or "
        "'2 cups flour' or 'onion, diced'. The name becomes a catalog item and is "
        "matched against existing stock, so a count/unit/prose baked into it creates "
        "an item that can never match. `quantity` is for the ENTIRE recipe (matching "
        "`servings`), not per serving.",
    },
    {
        "task": "Create a recipe from parsed/OCR text (also name-based, one call)",
        "method": "POST",
        "path": "/api/recipes/ingest-parsed",
        "body": {
            "title": "Pancakes",
            "instructions": "Mix and cook.",
            "ingredients": [{"raw_line": "2 eggs", "name": "eggs", "quantity": 2, "unit": "each"}],
            "create_missing_items": True,
        },
        "notes": "Note the different field names here: `title` (not name), and each ingredient needs `raw_line`.",
    },
    {
        "task": "Get a recipe with its ingredients",
        "method": "GET",
        "path": "/api/recipes/{recipe_id}",
        "returns": {"recipe": {"...": "..."}, "ingredients": [{"item_id": "...", "quantity_required": "2.000", "unit": "cup"}]},
    },
    {
        "task": "List recipes (with ingredient counts)",
        "method": "GET",
        "path": "/api/recipes",
    },
    {
        "task": "Update recipe metadata and/or ingredients",
        "method": "PUT",
        "path": "/api/recipes/{recipe_id}",
        "body": {"name": "New name", "ingredients": [{"name": "eggs", "quantity": 3, "unit": "each"}]},
        "notes": "Omit `ingredients` to change only metadata; send [] to clear them.",
    },
    {
        "task": "Replace a recipe's ingredients by catalog item id (low-level primitive)",
        "method": "PUT",
        "path": "/api/recipes/{recipe_id}/ingredients",
        "body": [{"item_id": "<uuid>", "quantity_required": 2, "unit": "cup"}],
        "notes": "A bare ARRAY, and it REPLACES the whole set. Uses `item_id` + `quantity_required` "
        "(not name/quantity). Prefer POST/PUT /api/recipes with `ingredients` for name-based work.",
    },
    {
        "task": "Delete a recipe",
        "method": "DELETE",
        "path": "/api/recipes/{recipe_id}",
    },
    {
        "task": "Add a catalog item",
        "method": "POST",
        "path": "/api/items",
        "body": {"name": "Olive Oil", "barcode": "0123456789012"},
    },
    {
        "task": "Scan a barcode (looks up Open Food Facts, optionally auto-creates the item)",
        "method": "POST",
        "path": "/api/barcodes/scan",
        "body": {"barcode": "852480007112", "auto_create": True},
    },
    {
        "task": "Add stock",
        "method": "POST",
        "path": "/api/inventory/add",
        "body": {"item_id": "<uuid>", "location_id": "<uuid>", "quantity": 1, "unit": "each"},
    },
    {
        "task": "Remove/consume stock (converts units — e.g. 1 cup from a gallon 'each')",
        "method": "POST",
        "path": "/api/inventory/remove",
        "body": {"item_id": "<uuid>", "location_id": "<uuid>", "quantity": 1, "unit": "cup"},
    },
    {
        "task": "Add a loose need to the shopping list (by name)",
        "method": "POST",
        "path": "/api/shopping-list",
        "body": {"name": "Milk", "quantity": 1, "unit": "gal"},
    },
    {
        "task": "Search stores for purchasable products (offers)",
        "method": "GET",
        "path": "/api/shopping-list/search",
        "query": {"q": "chili beans", "limit": 8, "provider": "kroger (optional — restrict to one source)"},
        "returns": {
            "offers": [
                {"provider": "kroger", "name": "...", "brand": "...", "size": "15 oz", "price": 1.29, "upc": "...", "image_url": "..."}
            ],
            "throttled": [],
        },
        "notes": "Food results (USDA) need a key; priced retailer results come from Kroger. Use provider=kroger for Kroger only. Search GENERIC terms ('olive oil', 'kidney beans') — a long exact product name over-constrains keyword search and can return nothing; pick the specific product from the results.",
    },
    {
        "task": "Pick an offer → create the catalog item and put it on the list",
        "method": "POST",
        "path": "/api/shopping-list/from-offer",
        "body": {"provider": "kroger", "name": "Kroger Chili Beans", "upc": "...", "size": "15 oz", "price": 1.29, "quantity": 2, "unit": "each", "item_id": "<existing catalog item to enrich, optional>"},
        "notes": "Pass the whole offer plus quantity/unit. Without item_id it creates/links a catalog item by UPC. **When shopping for a recipe's ingredient, pass that ingredient's item_id** so the offer enriches the EXISTING item (UPC/brand/size/image) instead of spawning a duplicate. entry_id resolves an existing loose need.",
    },
    {
        "task": "Send the pending shopping list to the Kroger cart (order)",
        "method": "POST",
        "path": "/api/shopping-list/kroger-cart",
        "returns": {
            "added": [{"upc": "...", "quantity": 2, "name": "Kroger Chili Beans"}],
            "skipped": [{"name": "Fresh Basil", "reason": "no Kroger UPC"}],
            "cart_url": "https://www.kroger.com/cart",
        },
        "notes": "Only entries carrying a Kroger UPC (from a chosen Kroger offer, or the item's barcode) are "
        "ordered; the rest are reported in `skipped`. Requires a Kroger account linked in-app under Integrations "
        "(one-time OAuth) — a 409 means not connected, 400 means the Kroger API keys aren't set.",
    },
    {
        "task": "Find which recipes you can/can't make now (missing ingredients across all locations)",
        "method": "GET",
        "path": "/api/recipes/suggestions",
        "returns": {"suggestions": [{"recipe_name": "Chili", "available_score": 0.6, "missing_items": ["kidney beans", "cumin"]}]},
        "notes": "The quickest way to see what a recipe is missing — pooled across every location.",
    },
    {
        "task": "List the household's eaters (needed before logging any meal)",
        "method": "GET",
        "path": "/api/household/profiles",
        "notes": "Meal logging and calorie reads are per-eater — every call below needs a profile_id "
        "from here. Creating profiles is admin-only; an agent key can read them but not add them.",
    },
    {
        "task": "Log a meal someone ate (calories/macros snapshotted at log time)",
        "method": "POST",
        "path": "/api/consumption/plate",
        "body": {
            "item_id": "<uuid>",
            "profile_id": "<uuid>",
            "quantity": 150,
            "unit": "g",
            "meal_type": "lunch",
        },
        "notes": "Omit location_id for off-pantry eating (a restaurant meal, a snack never in stock) — "
        "calories still count, nothing is deducted. Pass location_id to ALSO deduct it from that "
        "location's stock. Calories only resolve if the amount converts to grams, so prefer unit='g' "
        "when the item has no serving size or per-each weight.",
    },
    {
        "task": "Log eating a portion of a recipe you cooked",
        "method": "POST",
        "path": "/api/consumption/plate-recipe",
        "body": {"recipe_id": "<uuid>", "profile_id": "<uuid>", "servings": 2, "meal_type": "dinner"},
        "notes": "Nutrition comes from the recipe's own ingredients, scaled to `servings`. Never deducts "
        "inventory — cooking already did that; eating a portion must not deduct twice.",
    },
    {
        "task": "Log something with no barcode that isn't in the catalog ('three hard boiled eggs')",
        "method": "GET",
        "path": "/api/items/search-nutrition",
        "query": {"q": "hard boiled egg"},
        "notes": "Search USDA generic foods, then POST /api/items/from-food-search {fdc_id, q} with the "
        "SAME q to materialise a lightweight barcode-less item, then plate it. Repeat calls reuse the "
        "same item rather than duplicating it.",
    },
    {
        "task": "Read one person's calories/macros for a day",
        "method": "GET",
        "path": "/api/consumption/calories",
        "query": {"profile_id": "<uuid>", "on": "2026-08-03", "tz": "America/New_York"},
        "notes": "ALWAYS send tz (an IANA zone). Without it the day is bucketed in UTC, which rolls over "
        "hours before the household's real midnight — an evening meal appears to vanish. `on` defaults "
        "to today. Entries with calories=null had no usable nutrition; `complete` says whether any were.",
    },
    {
        "task": "Aggregate calories/macros over a date range (a week, a month)",
        "method": "GET",
        "path": "/api/consumption/nutrition-summary",
        "query": {"profile_id": "<uuid>", "from": "2026-07-28", "to": "2026-08-03", "tz": "America/New_York"},
        "notes": "Returns per-day totals plus a range total and daily average. Omit profile_id for the "
        "whole household. Same tz rule as above.",
    },
    {
        "task": "Read a recipe's nutrition without logging anything",
        "method": "GET",
        "path": "/api/recipes/{recipe_id}/nutrition",
        "notes": "Total and per-serving, summed from the ingredients' per-100g figures. `unresolved` "
        "lists ingredients with no nutrition data and `complete` is false when any are — so a partial "
        "total is never mistaken for a whole one.",
    },
    {
        "task": "Undo a mistakenly logged meal",
        "method": "DELETE",
        "path": "/api/consumption/plate/{event_id}",
        "notes": "Restores any inventory it deducted and nets it out of the calorie total. Never a hard "
        "delete — both the mistake and its correction stay in the ledger. PATCH the same path with "
        "{quantity, unit} to correct the amount instead.",
    },
    {
        "task": "Turn a product URL into a catalog item + shopping/inventory entry",
        "method": "POST",
        "path": "/api/agent/ingest-product-url",
        "body": {"url": "https://...", "add_to_shopping": True},
    },
]

_WORKFLOWS: list[dict] = [
    {
        "goal": "Make a recipe for N servings and shop for what's missing",
        "steps": [
            "GET /api/recipes — find the recipe by name (create it via POST /api/recipes with ingredients if absent).",
            "GET /api/recipes/{id} — read recipe.servings and each ingredient's quantity_required + unit.",
            "Scale: needed = quantity_required * (target_servings / recipe.servings).",
            "GET /api/recipes/suggestions — read missing_items for that recipe (what you don't have on hand, pooled across locations).",
            "For each missing ingredient: note its item_id from the recipe; GET /api/shopping-list/search?q=<name>&provider=kroger; POST /api/shopping-list/from-offer with the offer, your scaled quantity, AND item_id=<that ingredient's item_id> so the recipe's item is enriched (not duplicated).",
        ],
        "notes": "Passing item_id is what keeps this from creating duplicate catalog items — the recipe's ingredient item gets the Kroger UPC/brand, and the list entry links to it.",
    },
]

_COMMON_MISTAKES: list[str] = [
    "Instructions: number the steps sequentially from 1 (delimiter optional — '1.' and "
    "a bare '1 ' both work). The sequence is what distinguishes a step from a line that "
    "merely begins with a digit, so skipped or repeated numbers stop being read as steps.",
    "Never mix ingredient lines into `instructions`. Scraped imports commonly leave "
    "'1 1/2 lb steak' sitting between steps — it won't create a bogus step, but it does "
    "get appended to the step above. Send ingredients in `ingredients`.",
    "Ingredient `name` is the ingredient alone. Sending '2 avocados' used to create a "
    "catalog item literally called '2 avocados' AND record the amount as the 1.0 "
    "default — the recipe then asked for half what it needed and never matched the "
    "avocado already in stock. A leading count (and a unit right after it) is now "
    "lifted out for you, but 'onion, diced' or 'freshly grated Parmesan' still become "
    "junk items: send 'onion' / 'Parmesan cheese'.",
    "Do not pre-create items for a recipe's ingredients. POST /api/recipes with "
    "`create_missing_items: true` resolves each name against existing stock first "
    "(exact, then canonical identity) and only creates what is genuinely new. "
    "Creating them yourself first is what produces duplicate generic items.",
    "Plural and singular resolve to the same item ('avocados' -> 'avocado'), so don't "
    "try to normalise names yourself — send them as the recipe writes them.",
    "`external_source` is an OBJECT: {\"source\": \"mealime\", \"id\": \"123\", \"url\": \"...\"}. "
    "There are no top-level external_id / external_url fields (422). Supplying it makes "
    "a re-import an UPDATE instead of a duplicate recipe.",
    "There is a bulk endpoint: POST /api/recipes/bulk — you do not need one call per recipe.",
    "There IS a meal-logging and calorie API: POST /api/consumption/plate and GET /api/consumption/calories. "
    "It is spelled 'consumption', not 'meals' — searching for /api/meals returns 404 and proves nothing.",
    "Fetch the schema from /api/openapi.json, not /openapi.json: deployments serve the web app at / and "
    "proxy only /api, so the bare path returns the frontend's HTML shell rather than the spec.",
    "Ingredient rows carry no nutrition themselves — the ITEM each row points at carries per-100g macros. "
    "Use GET /api/recipes/{id}/nutrition rather than concluding recipes have no calorie data.",
    "Recipes carry a `servings` count and ingredients carry `quantity_required` — scale by target_servings / recipe.servings yourself; there's no scaled-ingredients endpoint.",
    "To find what a recipe is missing, use GET /api/recipes/suggestions (missing_items) rather than diffing inventory by hand.",
    "Shopping search returns *offers* (products to buy). Use /from-offer to turn a chosen offer into a catalog item + list entry in one call.",
    "Search generic terms ('olive oil'), not exact/long product names — retailer keyword search over-constrains on full names and may return zero offers. Pick the specific product from the results.",
    "Recipe ingredients: pass them inside `ingredients` on POST/PUT /api/recipes. "
    "Extra/unknown fields now return 422 rather than being silently dropped.",
    "The ingredients sub-resource is PUT /api/recipes/{id}/ingredients, not POST — and there is no GET on it "
    "(ingredients come back from GET /api/recipes/{id}).",
    "The recipe edit page is at /recipes/{id}/edit, not /recipes/edit/{id}.",
    "PUT /api/recipes/{id}/ingredients takes a bare array and uses `item_id` + `quantity_required`; "
    "POST/PUT /api/recipes `ingredients` uses `name` (or item_id) + `quantity`.",
]


@router.get("/guide")
def agent_guide_route() -> dict:
    return {
        "about": "Task-oriented map of the PantryOS API for agents. See also /openapi.json and /docs.",
        "auth": {
            "header": "X-Agent-Key: <your agent key>",
            "note": "Mint one under Agent Keys in the app or POST /api/agent-keys. "
            "Also send X-License-Key if the deployment enforces licensing.",
        },
        "conventions": {
            "base_path": "/api",
            "ids": "UUID strings",
            "unknown_fields": "rejected with HTTP 422",
        },
        "tasks": _TASKS,
        "workflows": _WORKFLOWS,
        "common_mistakes": _COMMON_MISTAKES,
    }
