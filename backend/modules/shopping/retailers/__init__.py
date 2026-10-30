from modules.shopping.retailers.base import (
    CAP_AFFILIATE,
    CAP_CART_ADD,
    CAP_CART_LINK,
    CAP_PRODUCT_DETAILS,
    CAP_PRODUCT_LINK,
    CAP_PRODUCT_SEARCH,
    CAP_PURCHASE_HISTORY,
    CAP_RECEIPT_IMPORT,
    CAP_SEARCH_LINK,
    CAP_STORE_INVENTORY,
    CAP_STORE_SEARCH,
    RetailerAdapter,
)
from modules.shopping.retailers.registry import get_adapter, list_adapters

__all__ = [
    "CAP_AFFILIATE",
    "CAP_CART_ADD",
    "CAP_CART_LINK",
    "CAP_PRODUCT_DETAILS",
    "CAP_PRODUCT_LINK",
    "CAP_PRODUCT_SEARCH",
    "CAP_PURCHASE_HISTORY",
    "CAP_RECEIPT_IMPORT",
    "CAP_SEARCH_LINK",
    "CAP_STORE_INVENTORY",
    "CAP_STORE_SEARCH",
    "RetailerAdapter",
    "get_adapter",
    "list_adapters",
]
