"""Event handlers for Pantry-OS.

Side effects: audit logging, webhooks, AI suggestions.
Decoupled from main operations via event bus.
"""

from handlers.audit_handler import AuditLogHandler
from handlers.webhook_handler import WebhookHandler
from handlers.registration import register_default_handlers

__all__ = [
    "AuditLogHandler",
    "WebhookHandler", 
    "register_default_handlers",
]
