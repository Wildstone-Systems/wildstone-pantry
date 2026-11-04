"""Meal planning module metadata."""

MODULE_INFO = {
    "name": "mealplan",
    "purpose": "A household calendar of intended meals: what is planned for which day and meal.",
    "owns": ["meal_plan_entries"],
    "consumes_from": {
        "planning": ["get_recipe"],
        "catalog": ["get_item"],
        "household": ["get_profile", "resolve_actor_profile"],
    },
    "invariants": [
        "A plan never reserves stock; availability stays live.",
        "Every entry names a meal: a recipe, an item, or free text.",
        "Recipe and item entries show the live name; the title is the fallback if the source is deleted.",
        "Uncooked entries are left alone; nothing auto-skips or rolls forward.",
    ],
}
