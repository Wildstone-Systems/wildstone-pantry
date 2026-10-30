from modules.shopping.api.connector_admin import router as connector_admin_router
from modules.shopping.api.email_receipts import router as email_receipts_router
from modules.shopping.api.handoff import retailers_router
from modules.shopping.api.handoff import router as handoff_router
from modules.shopping.api.kroger_oauth import router as kroger_oauth_router
from modules.shopping.api.purchase_connectors import router as purchase_connectors_router
from modules.shopping.api.purchases import router as purchases_router
from modules.shopping.api.receipts import router as receipts_router
from modules.shopping.api.shopping_list import router as shopping_list_router

__all__ = [
    "connector_admin_router",
    "kroger_oauth_router",
    "purchases_router",
    "purchase_connectors_router",
    "receipts_router",
    "email_receipts_router",
    "handoff_router",
    "retailers_router",
    "shopping_list_router",
]
