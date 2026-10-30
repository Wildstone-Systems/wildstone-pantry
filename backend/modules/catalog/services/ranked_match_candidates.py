"""Identity-oriented retrieval; synonyms never rewrite canonical keys."""

import re

from modules.catalog.services import match_evidence
from modules.catalog.services.items import get_item, list_items
from modules.inventory.services import review_snapshot as inventory

SYNONYMS = {
    "chickpea": ("garbanzo", "garbanzos"),
    "chickpeas": ("garbanzo", "garbanzos"),
    "garbanzo": ("chickpea", "chickpeas"),
    "garbanzos": ("chickpea", "chickpeas"),
}
DISTRACTORS = {
    "flavor",
    "flavored",
    "flavoured",
    "blend",
    "seasoning",
    "style",
    "chips",
    "snack",
    "free",
    "without",
    "no",
    "non",
}


def tokens(name):
    return set(re.findall(r"[a-z0-9]+", name.casefold()))


def discover(db, requested_id, *, lock=False):
    from modules.catalog import ranked_match_repository

    if lock:
        ranked_match_repository.lock_source_scope(db)
    target = match_evidence.item_evidence(db, requested_id)
    target_tokens = tokens(target["name"])
    meaningful = target_tokens - match_evidence.DESCRIPTORS
    terms = sorted(meaningful, key=lambda word: (-len(word), word))[:3] or [target["name"]]
    terms += [synonym for word in sorted(target_tokens) for synonym in SYNONYMS.get(word, ())]
    if target["canonical_ingredient"]:
        terms.append(target["canonical_ingredient"])
    # Known aliases are admitted before the newest-first textual limit; an old
    # confirmed match must not disappear behind many newer name distractors.
    rows = {
        item.id: item
        for item in ranked_match_repository.confirmed_candidates(
            db, target["canonical_ingredient"], inventory.stocked_item_ids(db)
        )
    }
    for term in dict.fromkeys(terms):
        for item in list_items(db, search=term, in_stock_only=True, limit=50):
            rows[item.id] = item
    if match_evidence.stocked(db, requested_id):
        rows[requested_id] = get_item(db, requested_id)
    if lock:
        # Take every item/profile lock in a common UUID order, including target.
        locked = {
            item_id: match_evidence.item_evidence(db, item_id, lock=True)
            for item_id in sorted({requested_id, *rows}, key=str)
        }
        target = locked[requested_id]
    else:
        locked = {}
    ranked = []
    scope = []
    for item_id in sorted(rows, key=str):
        evidence = locked.get(item_id) or match_evidence.item_evidence(db, item_id)
        words = tokens(evidence["name"])
        synonym_overlap = any(word in words for token in target_tokens for word in SYNONYMS.get(token, ()))
        if item_id == requested_id:
            rank, reason = 0, "same_item"
        elif (
            evidence["canonical_provenance"] == "confirmed"
            and target["canonical_ingredient"]
            and target["canonical_ingredient"] == evidence["canonical_ingredient"]
        ):
            rank, reason = 1, "confirmed_identity"
        else:
            rank, reason = 2, "culinary_synonym" if synonym_overlap else "token_overlap"
        distractors = len((words - target_tokens) & DISTRACTORS)
        overlap = len(words & target_tokens) + int(synonym_overlap)
        scope.append({"item_id": evidence["item_id"], "source_fingerprint": evidence["source_fingerprint"]})
        ranked.append(
            (
                (rank, distractors, -overlap, len(words - target_tokens), evidence["name"].casefold(), str(item_id)),
                {"item": evidence, "retrieval_reason": reason},
            )
        )
    return {
        "requested": target,
        "scope": scope,
        "candidates": [row[1] for row in sorted(ranked, key=lambda row: row[0])[:3]],
    }
