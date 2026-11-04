"""Meal plan module manifest."""

from core.modules import ModuleManifest, register_module
from modules.mealplan.api import feed_admin_router, feed_router, router

# The calendar-feed routers come first: their literal paths must win over the
# main router's catch-alls, and the public .ics route carries no login.
manifest = register_module(
    ModuleManifest(name="mealplan", version="0.1.0", routers=[feed_admin_router, feed_router, router])
)
