"""Shipped retailer search-link seed rows (ADR 0019 retailer matrix).

Templates are data: seeding is idempotent and insert-missing-only, so an app
update can ship new retailers while never clobbering an admin's edits to
existing rows. ``status="verified"`` means the pattern was confirmed against
live/indexed pages at research time (2026-07-30); ``"unverified"`` needs a
one-time manual browser check (bot walls blocked server-side verification —
which never blocks the user's own browser, hence 403 ≠ broken).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RetailerLink

# (key, label, domain, search_template, encoder, status, notes)
SEED_ROWS: list[tuple[str, str, str, str, str, str, str | None]] = [
    ("kroger", "Kroger", "kroger.com", "https://www.kroger.com/search?query={q}", "plus", "unverified", None),
    ("walmart", "Walmart", "walmart.com", "https://www.walmart.com/search?q={q}", "plus", "verified", None),
    ("target", "Target", "target.com", "https://www.target.com/s?searchTerm={q}", "plus", "verified", None),
    (
        "instacart",
        "Instacart",
        "instacart.com",
        "https://www.instacart.com/store/s?k={q}",
        "plus",
        "unverified",
        "IDP applications closed as of 2026-07-30 — quarterly watch item.",
    ),
    ("costco", "Costco", "costco.com", "https://www.costco.com/CatalogSearch?keyword={q}", "plus", "unverified", None),
    ("aldi", "ALDI", "aldi.us", "https://shop.aldi.us/store/aldi/s?k={q}", "plus", "verified", "Storefront runs on Instacart white-label."),
    ("samsclub", "Sam's Club", "samsclub.com", "https://www.samsclub.com/search?q={q}", "plus", "verified", None),
    ("meijer", "Meijer", "meijer.com", "https://www.meijer.com/shopping/search.html?text={q}", "plus", "verified", None),
    ("hyvee", "Hy-Vee", "hy-vee.com", "https://www.hy-vee.com/aisles-online/search?search={q}", "plus", "verified", None),
    ("heb", "H-E-B", "heb.com", "https://www.heb.com/search?q={q}", "plus", "unverified", "No API, no Instacart, no affiliate — handoff-only."),
    ("publix", "Publix", "publix.com", "https://delivery.publix.com/store/publix/s?k={q}", "plus", "verified", "Delivery storefront is Instacart white-label."),
    ("wholefoods", "Whole Foods Market", "wholefoodsmarket.com", "https://www.wholefoodsmarket.com/search?text={q}", "plus", "verified", None),
    (
        "wegmans",
        "Wegmans",
        "wegmans.com",
        "https://shop.wegmans.com/search?search_term={q}",
        "plus",
        "verified",
        "2025 site migration in progress — retest periodically.",
    ),
    ("walgreens", "Walgreens", "walgreens.com", "https://www.walgreens.com/search/results.jsp?Ntt={q}", "plus", "verified", None),
    ("cvs", "CVS", "cvs.com", "https://www.cvs.com/search?searchTerm={q}", "plus", "unverified", None),
    ("lowes", "Lowe's", "lowes.com", "https://www.lowes.com/search?searchTerm={q}", "plus", "verified", None),
    ("homedepot", "The Home Depot", "homedepot.com", "https://www.homedepot.com/s/{q}", "percent", "verified", None),
    ("menards", "Menards", "menards.com", "https://www.menards.com/main/search.html?search={q}", "plus", "unverified", "Most store-contextual site on the list."),
    ("acehardware", "Ace Hardware", "acehardware.com", "https://www.acehardware.com/search?query={q}", "plus", "unverified", None),
    ("amazon", "Amazon", "amazon.com", "https://www.amazon.com/s?k={q}", "plus", "verified", None),
    ("bestbuy", "Best Buy", "bestbuy.com", "https://www.bestbuy.com/site/searchpage.jsp?st={q}", "plus", "verified", None),
    ("ebay", "eBay", "ebay.com", "https://www.ebay.com/sch/i.html?_nkw={q}", "plus", "verified", None),
]


def ensure_seed(db: Session) -> None:
    """Insert any seed retailer missing from retailer_links. Never updates an
    existing row — admin edits win over shipped defaults."""
    existing = {k for (k,) in db.execute(select(RetailerLink.key)).all()}
    missing = [row for row in SEED_ROWS if row[0] not in existing]
    if not missing:
        return
    for key, label, domain, template, encoder, status, notes in missing:
        db.add(
            RetailerLink(
                key=key,
                label=label,
                domain=domain,
                search_template=template,
                encoder=encoder,
                status=status,
                notes=notes,
            )
        )
    db.commit()
