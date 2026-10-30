"""Event registration and wiring.

Called at application startup to wire up event handlers.
"""

from core.events import get_event_bus
from handlers.audit_handler import AuditLogHandler
from handlers.webhook_handler import WebhookHandler

# NOTE: the former RestockSuggestionHandler stub is retired — restock is now a
# real feature: the shopping list reconciles auto entries from catalog restock
# thresholds against the inventory ledger (modules/shopping/services/shopping_list.py).


def _home_assistant_config(db) -> dict[str, str]:
    """Effective outbound-webhook config: in-app override over env (ADR 0009).

    Imported lazily so the handlers package stays importable without the app
    context (the resolver only runs inside a request, where a session exists).
    """
    from modules.shopping.services import connector_config

    return connector_config.get_effective(db, "home_assistant")


def register_default_handlers(webhook_url: str | None = None) -> None:
    """Register default event handlers on global event bus.

    Usage:
        from handlers import register_default_handlers
        register_default_handlers()  # Called at startup

    ``webhook_url`` is a static override used by tests/scripts; the normal path
    resolves the Home Assistant webhook config per event (no restart needed).
    """
    bus = get_event_bus()
    bus.register(AuditLogHandler(log_to_console=True))
    if webhook_url is not None:
        bus.register(WebhookHandler(webhook_url=webhook_url))
    else:
        bus.register(WebhookHandler(config_resolver=_home_assistant_config))
