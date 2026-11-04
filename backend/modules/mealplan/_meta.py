"""Meal planning module metadata."""

MODULE_INFO = {
    "name": "mealplan",
    "purpose": "A household calendar of intended meals: what is planned for which day and meal.",
    "owns": ["meal_plan_entries", "meal_plan_excluded_items"],
    "consumes_from": {
        "planning": ["get_recipe", "recipe_availability"],
        "catalog": ["get_item"],
        "inventory": ["feed.household_stock"],
        "shopping": ["add_entry_once"],
        "household": ["get_profile", "resolve_actor_profile"],
    },
    "invariants": [
        "A plan never reserves stock; availability stays live.",
        "Every entry names a meal: a recipe, an item, or free text.",
        "Recipe and item entries show the live name; the title is the fallback if the source is deleted.",
        "Uncooked entries are left alone; nothing auto-skips or rolls forward.",
        "The shopping gap is pushed only on request, never automatically, and never duplicates list entries.",
    ],
}
