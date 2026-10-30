"""Planning module metadata."""

MODULE_INFO = {
    "name": "planning",
    "purpose": "Recipes + ingredients, cooking (deducts inventory), recipe import, insights.",
    "owns": ["recipes", "recipe_ingredients", "recipe_cook_events", "consumption_trends", "restock_suggestions"],
    "consumes_from": {"inventory": ["get_location", "remove_inventory"], "automation": ["parse_recipe_text"]},
    "invariants": ["Cooking a recipe deducts ingredient inventory transactionally."],
}
