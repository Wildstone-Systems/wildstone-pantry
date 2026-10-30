from app.models.agent_key import AgentKey
from app.models.app_setting import AppSetting
from app.models.device_key import DeviceKey
from app.models.entitlement_state import EntitlementState
from app.models.integration_connection import IntegrationConnection
from app.models.auth_token import AuthToken
from app.models.barcode_alias import BarcodeAlias
from app.models.base import Base
from app.models.category import Category
from app.models.connector_config import ConnectorConfig
from app.models.consumption_trend import ConsumptionTrend
from app.models.container import Container
from app.models.feedback import Feedback
from app.models.household_profile import HouseholdProfile
from app.models.ingredient_reference import IngredientReference
from app.models.ingredient_substitution import IngredientSubstitution
from app.models.inventory import Inventory
from app.models.inventory_location import InventoryLocation
from app.models.inventory_transaction import InventoryTransaction
from app.models.item import Item
from app.models.purchase import Purchase
from app.models.purchase_line_item import PurchaseLineItem
from app.models.receipt import Receipt
from app.models.receipt_line_item import ReceiptLineItem
from app.models.recipe import Recipe
from app.models.recipe_cook_event import RecipeCookEvent
from app.models.recipe_image import RecipeImage
from app.models.recipe_ingredient import RecipeIngredient
from app.models.restock_suggestion import RestockSuggestion
from app.models.retailer_handoff import RetailerHandoffItem, RetailerHandoffSession
from app.models.retailer_link import RetailerLink
from app.models.retailer_product_map import RetailerProductMap
from app.models.shopping_list_item import ShoppingListItem
from app.models.user import User
from app.models.webhook_event import WebhookEvent

# Inventory event-sourcing tables (ADR 0002) live in the inventory module but are
# registered here so they are part of Base.metadata (Alembic + test harness).
from modules.inventory.models import (  # noqa: E402
    InventoryBalance,
    InventoryEvent,
    InventoryLot,
    InventoryProjectionState,
)

# Planning-owned tables (ADR 0026 §6), registered for the same reason.
from modules.planning.models import CookSession, CookTimer, IngredientMatchReview, RankedIngredientReview  # noqa: E402
from modules.automation.models import ClassifierEvaluation  # noqa: E402
from modules.catalog.models import ItemProductEvidence  # noqa: E402

# Wildstone integration tables (ADR 0026), registered for the same reason.
from modules.wildstone.models import (  # noqa: E402
    WildstoneMemberLink,
    WildstoneOutbox,
    WildstoneRelayState,
    WildstoneStockFlag,
)

__all__ = [
    "ItemProductEvidence",
    "AgentKey",
    "AppSetting",
    "DeviceKey",
    "EntitlementState",
    "IntegrationConnection",
    "AuthToken",
    "BarcodeAlias",
    "Base",
    "Category",
    "ConnectorConfig",
    "ConsumptionTrend",
    "Container",
    "CookSession",
    "CookTimer",
    "Feedback",
    "HouseholdProfile",
    "IngredientReference",
    "IngredientSubstitution",
    "Inventory",
    "InventoryBalance",
    "InventoryEvent",
    "InventoryLocation",
    "InventoryLot",
    "InventoryProjectionState",
    "InventoryTransaction",
    "Item",
    "Purchase",
    "PurchaseLineItem",
    "Receipt",
    "ReceiptLineItem",
    "RestockSuggestion",
    "Recipe",
    "RecipeCookEvent",
    "RecipeImage",
    "RecipeIngredient",
    "RetailerHandoffItem",
    "RetailerHandoffSession",
    "RetailerLink",
    "RetailerProductMap",
    "ShoppingListItem",
    "User",
    "WebhookEvent",
]

from modules.shopping.receipt_review_models import ReceiptReviewDraft  # noqa: E402,F401

from modules.shopping.purchase_review_models import PurchaseCsvClaim, PurchaseReviewDraft  # noqa: E402,F401
from modules.planning.models.substitution_review import SubstitutionReview  # noqa: E402,F401
