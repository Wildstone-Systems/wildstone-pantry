"""PantryOS MCP server — exposes the kitchen as agent tools.

Run this in its **own virtualenv** (its `starlette`/`mcp` deps conflict with the
backend's pinned FastAPI stack — see mcp_server/README.md). It talks to a running
PantryOS over HTTP using an agent key.

Config (environment):
  PANTRYOS_BASE_URL     e.g. http://localhost:8000  (or your Tailscale URL)
  PANTRYOS_AGENT_KEY    a key minted under "Agent Keys" (X-Agent-Key)
  PANTRYOS_LICENSE_KEY  optional, if the deployment enforces a license

Usage:  python -m mcp_server.server   (stdio transport)
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_server.client import PantryOSClient

mcp = FastMCP("PantryOS")


@lru_cache(maxsize=1)
def _client() -> PantryOSClient:
    try:
        base_url = os.environ["PANTRYOS_BASE_URL"]
        agent_key = os.environ["PANTRYOS_AGENT_KEY"]
    except KeyError as exc:  # pragma: no cover - config error surfaced to the user
        raise RuntimeError(f"Missing required environment variable: {exc}") from exc
    return PantryOSClient(
        base_url=base_url,
        agent_key=agent_key,
        license_key=os.environ.get("PANTRYOS_LICENSE_KEY"),
    )


@mcp.tool()
def ingest_product_url(
    url: str,
    add_to_shopping: bool = True,
    add_to_inventory: bool = False,
    location_id: str | None = None,
    container_id: str | None = None,
    quantity: float = 1,
    unit: str = "each",
) -> Any:
    """Add a product from a web link. Extracts the product, upserts a catalog
    item, and adds it to the shopping list and/or inventory. Pass location_id to
    put it straight into inventory; otherwise it lands on the shopping list to buy
    later (and is checked off automatically when you log the purchase/receipt)."""
    return _client().ingest_product_url(
        url=url,
        add_to_shopping=add_to_shopping,
        add_to_inventory=add_to_inventory,
        location_id=location_id,
        container_id=container_id,
        quantity=quantity,
        unit=unit,
    )


@mcp.tool()
def add_to_shopping_list(
    name: str | None = None,
    item_id: str | None = None,
    quantity: float | None = None,
    unit: str = "each",
) -> Any:
    """Add a need to the shopping list — a free-text name, or an existing catalog
    item_id. Re-adding the same catalog item updates the open need instead of
    duplicating it."""
    return _client().add_to_shopping_list(name=name, item_id=item_id, desired_quantity=quantity, unit=unit)


@mcp.tool()
def list_shopping_list(status: str | None = None) -> Any:
    """List shopping-list items. Optional status filter: needed | purchased | dismissed."""
    return _client().list_shopping(status=status)


@mcp.tool()
def mark_shopping_item(shopping_item_id: str, status: str) -> Any:
    """Set a shopping item's status: needed | purchased | dismissed."""
    return _client().set_shopping_status(shopping_item_id, status)


@mcp.tool()
def add_inventory(
    item_id: str,
    location_id: str,
    quantity: float,
    unit: str = "each",
    expiration_date: str | None = None,
    container_id: str | None = None,
) -> Any:
    """Add stock of a catalog item to a location (optionally a container within
    it), with an optional expiration_date (YYYY-MM-DD)."""
    return _client().add_inventory(
        item_id=item_id,
        location_id=location_id,
        quantity=quantity,
        unit=unit,
        expiration_date=expiration_date,
        container_id=container_id,
    )


@mcp.tool()
def list_inventory(location_id: str) -> Any:
    """List current on-hand inventory for a location."""
    return _client().list_inventory(location_id)


@mcp.tool()
def whats_expiring(location_id: str, within_days: int = 7) -> Any:
    """List lots at a location expiring within N days (includes already-expired)."""
    return _client().whats_expiring(location_id, within_days)


@mcp.tool()
def list_locations() -> Any:
    """List storage locations (their ids are needed by the inventory tools)."""
    return _client().list_locations()


@mcp.tool()
def list_containers(location_id: str | None = None) -> Any:
    """List containers (sub-divisions of a location), optionally filtered to one location."""
    return _client().list_containers(location_id)


@mcp.tool()
def lookup_barcode(barcode: str) -> Any:
    """Look up product details for a barcode/UPC."""
    return _client().lookup_barcode(barcode)


@mcp.tool()
def household_summary(within_days: int = 7) -> Any:
    """One-call household snapshot: counts of items expiring within N days,
    already expired, below their restock threshold, pending shopping entries,
    and active locations. Start here to decide what needs attention."""
    return _client().get_summary(within_days)


@mcp.tool()
def list_household_profiles() -> Any:
    """List the household's eaters (id, display_name, daily_calorie_target).

    Meal logging and calorie queries are per-eater, so start here to get a
    profile_id — log_meal and calories_for both require one.
    """
    return _client().list_household_profiles()


@mcp.tool()
def log_meal(
    item_id: str,
    profile_id: str,
    quantity: float | None = None,
    unit: str = "each",
    location_id: str | None = None,
    weight_grams: float | None = None,
) -> Any:
    """Log what someone ate. Records a calorie/macro snapshot against profile_id.

    location_id given = also deduct it from that location's pantry stock.
    Omitted = off-pantry (a restaurant meal, a snack that was never in stock) —
    still counted toward calories, nothing deducted.

    Units: calories only resolve if the amount can be converted to grams, which
    needs either a weight unit ('g'/'kg'), or an item with a serving size or
    per-each weight. When unsure, log by weight in grams.

    For something with no barcode and not in the catalog ("three hard boiled
    eggs"), call search_food first, then item_from_food_search to get an item_id.
    """
    return _client().log_meal(
        item_id=item_id,
        profile_id=profile_id,
        quantity=quantity,
        unit=unit,
        location_id=location_id,
        weight_grams=weight_grams,
    )


@mcp.tool()
def delete_meal(event_id: str) -> Any:
    """Undo a mistakenly logged meal (the event_id returned by log_meal).
    Restores any inventory it deducted and nets it out of the calorie total."""
    return _client().delete_meal(event_id)


@mcp.tool()
def calories_for(profile_id: str, on: str | None = None, tz: str | None = None) -> Any:
    """One person's calorie/macro intake for a day: total, target, and every entry.

    `on` is a YYYY-MM-DD calendar day (default today). ALWAYS pass `tz` as an
    IANA zone (e.g. "America/New_York") — without it the day is bucketed in UTC,
    which rolls over hours before the household's real midnight and makes an
    evening's meals appear to vanish.
    """
    return _client().calories_for(profile_id, on=on, tz=tz)


@mcp.tool()
def household_calories(on: str | None = None, tz: str | None = None) -> Any:
    """Everyone's calorie totals vs target for a day. See calories_for re: `tz`."""
    return _client().household_calories(on=on, tz=tz)


@mcp.tool()
def search_food(query: str, limit: int = 10) -> Any:
    """Search USDA's generic-food database by name, for logging something with no
    barcode that isn't in the catalog ("hard boiled egg"). Returns candidates with
    an fdc_id and per-100g macros; pass the chosen fdc_id to item_from_food_search."""
    return _client().search_food(query, limit=limit)


@mcp.tool()
def item_from_food_search(fdc_id: int, query: str) -> Any:
    """Turn a search_food result into a catalog item you can log_meal against.

    Pass the SAME `query` used for search_food. Creates a lightweight,
    barcode-less item (never added to inventory) and reuses it on repeat calls,
    so logging the same food twice doesn't create duplicates.
    """
    return _client().item_from_food_search(fdc_id=fdc_id, query=query)


@mcp.tool()
def list_recipes() -> Any:
    """List saved recipes (id, name, servings)."""
    return _client().list_recipes()


@mcp.tool()
def recipe_nutrition(recipe_id: str) -> Any:
    """A recipe's calories/protein/fat/carbs — total and per serving — summed from
    its ingredients' per-100g nutrition. `unresolved` lists ingredients missing
    nutrition data, so a partial total is never mistaken for a complete one."""
    return _client().recipe_nutrition(recipe_id)


@mcp.tool()
def get_recipe(recipe_id: str) -> Any:
    """Get a recipe with its ingredient list."""
    return _client().get_recipe(recipe_id)


@mcp.tool()
def suggest_recipes(location_id: str | None = None) -> Any:
    """Suggest recipes cookable from current inventory — household-wide, or
    limited to one location's stock when location_id is given."""
    return _client().suggest_recipes(location_id)


@mcp.tool()
def cook_recipe(
    recipe_id: str,
    location_id: str | None = None,
    servings: int = 1,
    leftover_servings: int = 0,
    note: str | None = None,
    servings_made: float | None = None,
    output_weight_grams: float | None = None,
    output_portions: int | None = None,
    output_portion_size: float | None = None,
    output_portion_unit: str | None = None,
    scale_to_ingredient_id: str | None = None,
    allow_short_ingredients: bool = False,
) -> Any:
    """Cook a recipe: deducts its ingredients from inventory (first-expiring-first
    across locations when location_id is omitted). leftover_servings > 0 stores
    the surplus as a leftovers item.

    `servings` is a BATCH MULTIPLIER (2 = two whole recipes); `servings_made` is
    absolute servings and wins when given. `scale_to_ingredient_id` beats both:
    it sizes the whole batch from what's on hand for that one ingredient line —
    "use all the pork I have" — and scales every other line to match. Use it when
    the user names an ingredient to use up rather than a quantity to make.

    `allow_short_ingredients` lets a line that runs short be capped at what's on
    hand instead of failing the cook — a bigger roast with the same jar of rub.
    It changes the dish, so pass it only when the user has said they're happy to
    make do; the response's `capped_ingredients` names every line that was
    trimmed, and that is worth repeating back to them.

    Record what the cook actually observed rather than what the recipe predicts:

    * `output_weight_grams` — what the finished dish weighed. This REPLACES the
      recipe's declared yield (it is not scaled by servings), so pass it whenever
      the dish was weighed. Only used where the output item is measured by weight
      or volume; beside a counted yield ("2 loaves weighing 850 g") it stays
      metadata.
    * `output_portions` / `output_portion_size` / `output_portion_unit` — how the
      yield was packed. `output_portions` alone splits the whole yield evenly;
      adding a size makes that many containers of exactly that size and leaves
      any remainder loose. Each container can then be finished or discarded on
      its own. Pass these ONLY when the user said how it was packed — there is no
      default, and inventing a tub count invents facts about their fridge.
    """
    return _client().cook_recipe(
        recipe_id,
        location_id=location_id,
        servings=servings,
        leftover_servings=leftover_servings,
        note=note,
        servings_made=servings_made,
        output_weight_grams=output_weight_grams,
        output_portions=output_portions,
        output_portion_size=output_portion_size,
        output_portion_unit=output_portion_unit,
        scale_to_ingredient_id=scale_to_ingredient_id,
        allow_short_ingredients=allow_short_ingredients,
    )


@mcp.tool()
def start_cooking(recipe_id: str, servings: float | None = None) -> Any:
    """Start cooking a recipe step by step (a cook-along). Returns step 1 and the
    ingredient list scaled to `servings` (absolute servings to make; omit to cook
    the recipe as written).

    Nothing is taken out of the pantry until finish_cooking, so starting,
    moving between steps and stopping are all free. Starting a new recipe stops
    any other one in progress. Read the step_text aloud; step_ingredients are the
    amounts that step mentions.
    """
    return _client().start_cooking(recipe_id, servings=servings)


@mcp.tool()
def current_step() -> Any:
    """What step the cook is on right now ("where was I?"), or null when nothing
    is being cooked."""
    return _client().active_cook_session()


@mcp.tool()
def next_step() -> Any:
    """Move to the next step and return it. On the last step it stays there
    (is_last_step is true) — that's the moment to offer finish_cooking."""
    return _client().move_cook_step("next")


@mcp.tool()
def previous_step() -> Any:
    """Go back one step and return it."""
    return _client().move_cook_step("previous")


@mcp.tool()
def go_to_step(step: int) -> Any:
    """Jump to a step by its number (1 = first) and return it. To hear the
    current step again, call current_step instead."""
    return _client().move_cook_step("goto", step=step)


@mcp.tool()
def complete_step(step: int | None = None) -> Any:
    """Tick off a step ("done, what's next?") and move on to the next step not
    yet done. Without `step`, it ticks off the current step. The answer has
    steps_left; when it is 0, offer finish_cooking. The kitchen tablet shows
    the tick straight away."""
    return _client().complete_cook_step(step=step)


@mcp.tool()
def read_all_steps() -> Any:
    """Every step of the cook in progress, with which are done — for "what's
    left?" or "read me the whole recipe". Returns the session; its `steps` list
    has number, text and done."""
    return _client().active_cook_session()


@mcp.tool()
def set_timer(minutes: float, label: str | None = None) -> Any:
    """Start a kitchen timer: `minutes` may be fractional (0.5 = 30 seconds).
    `label` names it ("pasta"); omitted, it is named after the step. While a
    recipe is being cooked the timer is tied to its current step, whose
    step_timers suggest durations from the step's own words ("simmer 20
    minutes"). The tablet shows and rings it; set the timer, don't count down
    yourself."""
    return _client().set_timer(max(1, round(minutes * 60)), label=label)


@mcp.tool()
def list_timers() -> Any:
    """Timers running or ringing, soonest first, each with remaining_seconds
    and state ("running" or "ringing")."""
    return _client().list_timers()


@mcp.tool()
def stop_timer(label: str | None = None, timer_id: str | None = None) -> Any:
    """Stop a timer by its label ("the pasta timer") or id: cancels one still
    running, silences one ringing. With neither, stops the only timer there
    is; if several match, it asks which instead of guessing."""
    return _client().stop_timer(timer_id=timer_id, label=label)


@mcp.tool()
def finish_cooking(location_id: str | None = None) -> Any:
    """Finish the cook-along: takes the (scaled) ingredients out of the pantry —
    the only cook-along step that changes stock. Confirm with the person first.
    Cooks what was set up on the tablet (amounts changed, ingredients left out).
    location_id picks where to take them from; omitted = first-expiring-first
    across every location."""
    return _client().finish_cooking(location_id=location_id)


@mcp.tool()
def stop_cooking() -> Any:
    """Stop the cook-along without finishing. Nothing is taken from the pantry."""
    return _client().stop_cooking()


@mcp.tool()
def consumption_trends(location_id: str, lookback_days: int = 30) -> Any:
    """Per-item consumption trends at a location over the lookback window."""
    return _client().get_trends(location_id, lookback_days)


@mcp.tool()
def restock_suggestions(location_id: str) -> Any:
    """Items at a location that look like they need restocking."""
    return _client().get_restock_suggestions(location_id)


@mcp.tool()
def list_purchases() -> Any:
    """List recorded purchases (receipts/CSV imports), newest first."""
    return _client().list_purchases()


def main() -> None:
    """Run the server: stdio by default, streamable HTTP when configured.

    PANTRYOS_MCP_TRANSPORT=http (with optional PANTRYOS_MCP_HOST/PORT) starts a
    long-running HTTP endpoint a remote agent — or Home Assistant's MCP client —
    can connect to, instead of being spawned per-session over stdio.
    """
    transport = os.environ.get("PANTRYOS_MCP_TRANSPORT", "stdio").lower()
    if transport in {"http", "streamable-http"}:
        mcp.settings.host = os.environ.get("PANTRYOS_MCP_HOST", "127.0.0.1")
        mcp.settings.port = int(os.environ.get("PANTRYOS_MCP_PORT", "8765"))
        mcp.run(transport="streamable-http")
    elif transport == "stdio":
        mcp.run()
    else:
        raise RuntimeError(f"Unknown PANTRYOS_MCP_TRANSPORT: {transport!r} (use 'stdio' or 'http')")


if __name__ == "__main__":
    main()
