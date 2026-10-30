# NOTE: catalog, inventory, and shopping routers live in their feature modules
# and are mounted via ModuleManifests, not from here. Only platform/cross-cutting
# routers remain in app/routes during the migration.
from app.routes.activation import router as activation_router
from app.routes.agent_guide import router as agent_guide_router
from app.routes.agent_keys import router as agent_keys_router
from app.routes.auth import router as auth_router
from app.routes.devices import router as devices_router
from app.routes.entitlement import router as entitlement_router
from app.routes.feedback import router as feedback_router
from app.routes.health import router as health_router
from app.routes.household import router as household_router
from app.routes.oidc import router as oidc_router
from app.routes.setup import router as setup_router
from app.routes.system import router as system_router

__all__ = [
    "activation_router",
    "agent_guide_router",
    "agent_keys_router",
    "auth_router",
    "devices_router",
    "entitlement_router",
    "feedback_router",
    "health_router",
    "household_router",
    "oidc_router",
    "setup_router",
    "system_router",
]
