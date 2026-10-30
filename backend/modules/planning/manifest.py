"""Planning module manifest."""

from core.modules import ModuleManifest, register_module
from modules.planning.api.ingredient_match_reviews import router as ingredient_match_reviews_router
from modules.planning.api.ranked_ingredient_reviews import router as ranked_ingredient_reviews_router
from modules.planning.api.substitution_reviews import router as substitution_reviews_router
from modules.planning.api import ad_hoc_router, cook_sessions_router, cook_timers_router, insights_router, recipes_router, substitutions_router

manifest = register_module(
    ModuleManifest(
        name="planning",
        version="0.1.0",
        # ad_hoc BEFORE recipes: matching is declaration-ordered, and the
        # literal /recipes/ad-hoc paths must win over /recipes/{recipe_id}
        # (which would otherwise match first and 422 on the non-UUID).
        routers=[substitution_reviews_router, ranked_ingredient_reviews_router, ingredient_match_reviews_router, ad_hoc_router, recipes_router, insights_router, substitutions_router, cook_sessions_router, cook_timers_router],
    )
)
