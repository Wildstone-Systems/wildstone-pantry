"""Human recipe-specific associations; advisory models never change pantry truth."""

import copy
import hashlib
import hmac
import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.db.session import SessionLocal
from modules.automation import culinary_service
from modules.automation.culinary_questions import context_request, decode_answers, identity_request
from modules.automation.providers.classifier_common import ClassifierFailure
from modules.catalog.services import match_evidence as catalog
from modules.planning import ingredient_match_repository as repository
from modules.planning.schemas.ingredient_match_review import EvidenceFacts

TTL = timedelta(minutes=15)
UNKNOWN_NAME = re.compile(r"\b(unlabeled|unlabelled|unknown|unidentified|chk|brst|crm|misc)\b", re.I)
FORMS = re.compile(r"\b(powder|ground|whole|diced|crushed|shelled|boneless|liquid|paste|flour|seeds|flakes)\b", re.I)
PREPARATIONS = re.compile(r"\b(raw|cooked|frozen|dried|dry|canned|fresh|roasted|smoked)\b", re.I)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _packet(db, recipe_id, ingredient_id, candidate_id, facts, *, lock=False):
    ingredient, recipe = repository.context(db, recipe_id, ingredient_id, lock=lock)
    # Catalog owns item reads/locks. Stable lock order avoids two opposite reviews
    # taking product/target row locks in opposite orders on PostgreSQL.
    items = {
        key: catalog.item_evidence(db, key, lock=lock) for key in sorted({candidate_id, ingredient.item_id}, key=str)
    }
    product, target = items[candidate_id], items[ingredient.item_id]
    facts = EvidenceFacts.model_validate(facts).model_dump(exclude_unset=True)
    name = product["name"]
    form, preparation = FORMS.search(name), PREPARATIONS.search(name)
    defaults = {
        "identity": None if UNKNOWN_NAME.search(name) else name,
        "base_ingredient": None,
        "form": form.group(0).lower() if form else None,
        "preparation": preparation.group(0).lower() if preparation else None,
        "composition": (product["ingredients_text"] or "")[:1000] or None,
        "recipe_role": (recipe.instructions or "")[:500] or None,
    }
    metadata = product.get("structured_evidence")
    saved = metadata["facts"] if metadata and not metadata["stale"] else {}
    resolved = {key: facts[key] if key in facts else saved.get(key) or defaults[key] for key in defaults}
    sources = {
        key: "user_provided"
        if key in facts
        else "saved_user_provided"
        if saved.get(key)
        else "catalog_ingredients"
        if key == "composition" and defaults[key]
        else "catalog_name"
        if defaults[key]
        else "unknown"
        for key in defaults
    }
    # Recipe context is supplied source text, never a verified ingredient claim.
    if "recipe_role" not in facts:
        sources["recipe_role"] = "recipe_context" if defaults["recipe_role"] else "unknown"
    source = {
        "name": recipe.name,
        "instructions": recipe.instructions,
        "source_text": recipe.source_text,
        "item_id": str(ingredient.item_id),
        "quantity": str(ingredient.quantity_required),
        "unit": ingredient.unit,
        "optional": ingredient.optional,
        "quantity_mode": ingredient.quantity_mode,
        "yield_basis": ingredient.yield_basis,
    }
    return {
        "product": product,
        "requested": target,
        "recipe_id": str(recipe_id),
        "ingredient_id": str(ingredient_id),
        "recipe_name": recipe.name[:200],
        "recipe_instructions": (recipe.instructions or "")[:2000] or None,
        "recipe_source_fingerprint": digest(source),
        "recipe_ingredient_line": target["name"],
        "quantity": str(ingredient.quantity_required),
        "unit": ingredient.unit,
        "facts": facts,
        "resolved": resolved,
        "fact_sources": sources,
    }


def _missing(packet):
    values = packet["resolved"]
    required = ("identity",) if _exact(packet) else ("identity", "preparation", "form")
    return [
        key
        for key in required
        if not values.get(key)
        or UNKNOWN_NAME.search(values[key])
        or values[key].strip().lower() in {"unknown", "?", "n/a"}
    ]


def _next_clarification(missing):
    if not missing:
        return None
    field = missing[0]
    questions = {
        "identity": "What product is this? Check its label or another reliable product description.",
        "preparation": "How is this product prepared or preserved (for example raw, cooked, dried, frozen or canned)?",
        "form": "What physical form is this product in (for example whole, ground, powder, liquid or pieces)?",
    }
    return {"field": field, "question": questions[field], "reason_code": "missing_identity_evidence"}


def _exact(packet):
    product, target = packet["product"], packet["requested"]
    if product["item_id"] == target["item_id"]:
        return True
    # A shared canonical identity alone collapses meaningful forms/preparations.
    # Alias bypass requires supplied matching composition AND explicit matching
    # physical/preparation descriptors on both records; otherwise compare/review.
    if not (
        product["canonical_provenance"] == target["canonical_provenance"] == "confirmed"
        and product["canonical_ingredient"]
        and product["canonical_ingredient"] == target["canonical_ingredient"]
        and product["ingredients_text"]
        and product["ingredients_text"] == target["ingredients_text"]
    ):
        return False
    for pattern in (FORMS, PREPARATIONS):
        left, right = pattern.search(product["name"]), pattern.search(target["name"])
        if not left or not right or left.group().casefold() != right.group().casefold():
            return False
    return True


def candidates(db, recipe_id, ingredient_id, *, query=""):
    ingredient, _recipe = repository.context(db, recipe_id, ingredient_id)
    return {
        "recipe_id": str(recipe_id),
        "ingredient_id": str(ingredient_id),
        "requested": catalog.item_evidence(db, ingredient.item_id),
        "candidates": catalog.candidates(db, ingredient.item_id, query=query),
        "requires_admin_review": True,
    }


def create(db, recipe_id, ingredient_id, command, actor_id):
    repository.context(db, recipe_id, ingredient_id)
    if not catalog.stocked(db, command.candidate_item_id):
        raise LookupError("Choose an active in-stock candidate")
    head = repository.ensure_scope(db, ingredient_id)
    scope_revision = head.revision
    packet = _packet(
        db, recipe_id, ingredient_id, command.candidate_item_id, command.facts.model_dump(exclude_unset=True)
    )
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    row = repository.create(
        db,
        id=uuid4(),
        recipe_id=recipe_id,
        ingredient_id=ingredient_id,
        candidate_item_id=command.candidate_item_id,
        actor_id=actor_id,
        created_at=now,
        expires_at=now + TTL,
        revision=0,
        status="pending",
        token_hash=digest(token),
        document={
            "packet": packet,
            "evidence_hash": digest(packet),
            "scope_revision": scope_revision,
            "policy": culinary_service.review_policy(db),
            "predictions": [],
            "provider_requests": 0,
            "reason_codes": [],
            "selected_source_item_id": None,
            "confirmed_at": None,
        },
    )
    return {**read(db, row.id, actor_id), "confirmation_token": token}


def _row(db, review_id, actor_id):
    row = repository.get(db, review_id)
    if row is None or (row.status != "confirmed" and row.actor_id != actor_id):
        raise LookupError("Review unavailable")
    return row


def _freshness(db, row):
    try:
        current = _packet(db, row.recipe_id, row.ingredient_id, row.candidate_item_id, row.document["packet"]["facts"])
        evidence_changed = digest(current) != row.document["evidence_hash"]
    except LookupError:
        evidence_changed = True
    policy_changed = culinary_service.review_policy(db) != row.document["policy"]
    head = repository.scope(db, row.ingredient_id)
    scope_changed = head is None or head.revision != row.document["scope_revision"]
    return evidence_changed, policy_changed, scope_changed, head


def read(db, review_id, actor_id):
    row = _row(db, review_id, actor_id)
    doc = row.document
    evidence_changed, policy_changed, scope_changed, head = _freshness(db, row)
    expired = row.expires_at.replace(tzinfo=UTC) <= datetime.now(UTC)
    missing = _missing(doc["packet"])

    reasons = list(doc["reason_codes"])
    model_negative = any(p["purpose"] == "recipe_matching" and p["label"] == "not_suitable" for p in doc["predictions"])
    if model_negative:
        reasons.append("model_recipe_not_suitable")
    if missing:
        reasons.append("missing_identity_evidence")
    if evidence_changed:
        reasons.append("source_changed")
    if policy_changed:
        reasons.append("policy_changed")
    if expired and row.status != "confirmed":
        reasons.append("review_expired")
    if scope_changed and row.status != "confirmed":
        reasons.append("chosen_item_changed")
    unresolved = any(p["purpose"] == "identification" and p["label"] == "insufficient" for p in doc["predictions"])
    usable = row.status in {"pending", "interrupted"} and not (
        expired or evidence_changed or policy_changed or scope_changed or missing or unresolved
    )
    readiness = (
        "confirmed"
        if row.status == "confirmed"
        else "needs_information"
        if missing or unresolved
        else "not_recommended"
        if model_negative
        else "possible_match"
        if _exact(doc["packet"])
        or any(p["purpose"] == "recipe_matching" and p["label"] in {"direct", "possible"} for p in doc["predictions"])
        else "interrupted"
        if row.status == "interrupted"
        else "manual_review"
    )
    visible_predictions = doc["predictions"]
    if any(
        p["purpose"] == "recipe_matching" and p["label"] in {"not_suitable", "insufficient"}
        for p in visible_predictions
    ):
        visible_predictions = [p for p in visible_predictions if p["purpose"] != "adjustment"]
    return {
        "id": str(row.id),
        "recipe_id": str(row.recipe_id),
        "ingredient_id": str(row.ingredient_id),
        "candidate_item_id": str(row.candidate_item_id),
        "revision": row.revision,
        "scope_revision": doc["scope_revision"],
        "status": row.status,
        "evidence_hash": doc["evidence_hash"],
        "expires_at": row.expires_at,
        "packet": doc["packet"],
        "missing_fields": missing,
        "next_clarification": _next_clarification(["identity"] if unresolved else missing),
        "reason_codes": sorted(set(reasons)),
        "can_confirm": usable,
        "can_compare": usable
        and row.status == "pending"
        and doc["provider_requests"] == 0
        and doc["policy"]["enabled"]
        and not _exact(doc["packet"]),
        "readiness": readiness,
        "stale": evidence_changed or (scope_changed and row.status != "confirmed"),
        "prediction_stale": policy_changed or evidence_changed,
        "confirmation_required": True,
        "predictions": [] if policy_changed or evidence_changed else visible_predictions,
        "policy": doc["policy"],
        "provider_requests": doc["provider_requests"],
        "max_provider_requests": 2,
        "selected_source_item_id": doc["selected_source_item_id"],
        "confirmed_at": doc["confirmed_at"],
        "current_choice": bool(head and head.current_review_id == row.id),
    }


def history(db, recipe_id, ingredient_id, actor_id):
    repository.context(db, recipe_id, ingredient_id)
    return {
        "reviews": [read(db, row.id, actor_id) for row in repository.history(db, recipe_id, ingredient_id, actor_id)]
    }


def _binding(db, review_id, command, actor_id, *, allow_evaluating=False):
    row = _row(db, review_id, actor_id)
    if row.actor_id != actor_id or row.status not in (
        {"pending", "interrupted", "evaluating"} if allow_evaluating else {"pending", "interrupted"}
    ):
        raise ValueError("Human pending review required")
    if (
        row.revision != command.revision
        or not hmac.compare_digest(row.token_hash, digest(command.confirmation_token))
        or row.document["evidence_hash"] != command.evidence_hash
    ):
        raise ValueError("Review binding changed")
    changed = _freshness(db, row)[:3]
    if row.expires_at.replace(tzinfo=UTC) <= datetime.now(UTC) or any(changed):
        raise ValueError("Review expired or changed")
    return row


def confirm(db, review_id, command, actor_id):
    # Scope serialization precedes all evidence reads; both review and head CAS
    # commit atomically, so two old pending choices cannot replace each other.
    initial = _row(db, review_id, actor_id)
    # Lock order: configuration -> scope -> recipe/items.
    policy = culinary_service.review_policy(db, lock=True)
    repository.lock_scope(db, initial.ingredient_id)
    row = _binding(db, review_id, command, actor_id)
    if row.document["policy"] != policy:
        raise ValueError("Review policy changed")
    packet = _packet(
        db, row.recipe_id, row.ingredient_id, row.candidate_item_id, row.document["packet"]["facts"], lock=True
    )
    if digest(packet) != row.document["evidence_hash"] or not read(db, review_id, actor_id)["can_confirm"]:
        raise ValueError("Resolve current identity/preparation evidence first")
    doc = copy.deepcopy(row.document)
    doc.update(
        selected_source_item_id=str(row.candidate_item_id),
        confirmed_at=datetime.now(UTC).isoformat(),
        confirmation={
            "actor_id": str(actor_id),
            "scope": "recipe_ingredient_review_only",
            "method": "explicit_human_choice",
        },
    )
    if any(p["purpose"] == "recipe_matching" and p["label"] == "not_suitable" for p in doc["predictions"]):
        doc["reason_codes"].append("manual_choice_despite_known_conflict")
    repository.confirm(db, row, doc, expected_revision=command.revision)
    return read(db, review_id, actor_id)


def reject(db, review_id, command, actor_id):
    row = _binding(db, review_id, command, actor_id)
    repository.change(db, row, copy.deepcopy(row.document), status="rejected", expected_revision=command.revision)
    return read(db, review_id, actor_id)


async def compare(db, review_id, command, actor_id, disconnected):
    row = _binding(db, review_id, command, actor_id)
    view = read(db, review_id, actor_id)
    if not view["can_compare"]:
        return view
    policy = copy.deepcopy(row.document["policy"])
    doc = copy.deepcopy(row.document)
    revision = row.revision
    # Each explicit Compare may reserve at most two serial requests. A failed or
    # cancelled reservation is spent, never silently retried or resumed.
    for purpose in ("identification", "context"):
        row = repository.get(db, review_id)
        if purpose == "context" and not doc["packet"]["resolved"]["recipe_role"]:
            doc["reason_codes"].append("recipe_context_missing")
            repository.change(db, row, copy.deepcopy(doc), status="pending", expected_revision=revision)
            break
        if doc["provider_requests"] >= 2:
            break
        doc["provider_requests"] += 1
        repository.change(db, row, copy.deepcopy(doc), status="evaluating", expected_revision=revision)
        revision += 1

        async def stop_requested():
            if await disconnected():
                return True
            with SessionLocal() as fresh:
                current = repository.get(fresh, review_id)
                return (
                    current is None
                    or current.status != "evaluating"
                    or current.revision != revision
                    or current.expires_at.replace(tzinfo=UTC) <= datetime.now(UTC)
                    or any(_freshness(fresh, current)[:3])
                )

        failed = None
        try:
            request = identity_request(doc["packet"]) if purpose == "identification" else context_request(doc["packet"])
            answer = await culinary_service.evaluate_culinary(
                db, request, expected_policy=policy, stop_requested=stop_requested
            )
            doc["predictions"] += decode_answers(purpose, answer, policy["confidence"], policy["margin"])
        except ClassifierFailure as exc:
            failed = exc.code
        except ValueError:
            failed = "invalid_response"
        db.expire_all()
        row = repository.get(db, review_id)
        if row is None or row.revision != revision:
            raise ValueError("Review changed during comparison")
        changed = row.expires_at.replace(tzinfo=UTC) <= datetime.now(UTC) or any(_freshness(db, row)[:3])
        if failed or changed:
            doc["reason_codes"].append(failed or "source_changed")
            repository.change(db, row, doc, status="interrupted", expected_revision=revision)
            return read(db, review_id, actor_id)
        repository.change(db, row, copy.deepcopy(doc), status="pending", expected_revision=revision)
        revision += 1
        if purpose == "identification" and doc["predictions"][0]["label"] == "insufficient":
            break
    return read(db, review_id, actor_id)
