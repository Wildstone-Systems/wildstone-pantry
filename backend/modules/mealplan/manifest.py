"""Meal plan module manifest."""

from core.modules import ModuleManifest, register_module
from modules.mealplan.api import router

manifest = register_module(ModuleManifest(name="mealplan", version="0.1.0", routers=[router]))
