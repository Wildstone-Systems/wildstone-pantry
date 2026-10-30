"""Shopping module manifest."""

from core.modules import ModuleManifest, register_module
from modules.shopping.api import (
    connector_admin_router,
    email_receipts_router,
    handoff_router,
    kroger_oauth_router,
    purchase_connectors_router,
    purchases_router,
    receipts_router,
    retailers_router,
    shopping_list_router,
)

manifest = register_module(
    ModuleManifest(
        name="shopping",
        version="0.1.0",
        routers=[
            # Connectors router first: its static /purchases/connectors path must
            # win over purchases' /purchases/{purchase_id} catch-all.
            purchase_connectors_router,
            purchases_router,
            receipts_router,
            email_receipts_router,
            # Handoff first: its static /shopping-list/handoff prefix must win
            # over shopping-list's /shopping-list/{entry_id} catch-all.
            handoff_router,
            shopping_list_router,
            retailers_router,
            connector_admin_router,
            kroger_oauth_router,
        ],
    )
)
