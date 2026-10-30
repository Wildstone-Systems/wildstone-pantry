"""Explicit ranked advice and individually confirmed global ingredient aliases."""

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
from modules.automation import ingredient_identity_questions as questions
from modules.automation.providers.classifier_common import ClassifierFailure
from modules.catalog.services import ranked_match_candidates as catalog
from modules.inventory.services import review_snapshot as stock
from modules.planning import ranked_ingredient_repository as repository
from modules.planning.services.recipes import confirm_ingredient_item

UNKNOWN = re.compile(r"\b(unknown|unlabeled|unlabelled|unidentified|misc|chk|brst)\b", re.I)
MODIFIERS = {
    "blend",
    "flavored",
    "flavoured",
    "vanilla",
    "seasoning",
    "style",
    "smoked",
    "sweetened",
    "unsweetened",
    "free",
    "without",
    "no",
    "non",
}
FORM = {"ground", "whole", "powder", "paste", "liquid", "diced", "crushed"}
PREPARATION = {"raw", "cooked", "dried", "canned", "frozen", "fresh"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _descriptor(item):
    metadata = item.get("structured_evidence")
    facts = metadata["facts"] if metadata and not metadata["stale"] else {}
    return {
        **item,
        **{
            key: facts[key]
            for key in ("identity", "base_ingredient", "form", "preparation", "composition")
            if facts.get(key)
        },
    }


def _public_evidence(item):
    metadata = item.get("structured_evidence")
    current = metadata["facts"] if metadata and not metadata["stale"] else {}
    return {
        "brand": item["brand"],
        "ingredients_text": item["ingredients_text"],
        "source": item["source"],
        "current_facts": current,
        "facts_provenance": "saved_user_provided" if any(current.values()) else "unknown",
        "saved_facts_stale": bool(metadata and metadata["stale"]),
        "truncated": item.get("identity_evidence_truncated", False),
        "nutrition_basis": item["nutrition_basis"],
        "nutrition_source": item["nutrition_source"],
    }


def _snapshot(db, recipe_id, ingredient_id, *, lock=False, stock_snapshot=None):
    # Confirmation lock order: config -> stock tables -> review -> recipe ->
    # catalog rows. No inference occurs in a transaction holding these locks.
    target_id, source = repository.context(db, recipe_id, ingredient_id, lock=lock)
    discovery = catalog.discover(db, target_id, lock=lock)
    return {
        "recipe_source_hash": digest(source),
        "stock_hash": digest(stock_snapshot if stock_snapshot is not None else stock.snapshot(db)),
        "retrieval_scope_hash": digest(discovery["scope"]),
        "requested": discovery["requested"],
        "candidates": discovery["candidates"],
    }


def _result(candidate, requested):
    product = candidate["item"]
    base = {
        "candidate_item_id": product["item_id"],
        "product_name": product["name"],
        "requested_name": requested["name"],
        "product_evidence": _public_evidence(product),
        **{
            key: product[key]
            for key in ("canonical_ingredient", "canonical_provenance", "canonical_version", "canonical_revision")
        },
        "retrieval_reason": candidate["retrieval_reason"],
        "label": "insufficient_evidence",
        "decision_provenance": "abstention",
        "probability": None,
        "provider_confidence": None,
        "margin": None,
        "clears_threshold": False,
        "reason_codes": [],
        "can_confirm": False,
    }
    if any(item.get("identity_evidence_truncated") for item in (product, requested)):
        base["reason_codes"] = ["identity_evidence_truncated"]
        return base
    if candidate["retrieval_reason"] in {"same_item", "confirmed_identity"}:
        base.update(label="same_ingredient", decision_provenance=candidate["retrieval_reason"])
        return base
    left, right = _descriptor(product), _descriptor(requested)
    names = (left.get("identity") or left["name"], right.get("identity") or right["name"])
    if any(UNKNOWN.search(name) for name in names):
        base["reason_codes"] = ["missing_identity_evidence"]
        return base
    words = [catalog.tokens(name) for name in names]
    conflict = (words[0] ^ words[1]) & MODIFIERS
    for group in (FORM, PREPARATION):
        descriptors = [word & group for word in words]
        if all(descriptors) and descriptors[0] != descriptors[1]:
            conflict.add("form_or_preparation")
    # Different composition wording/order is unresolved evidence, not proof of
    # a blend: garbanzo/chickpea labels may describe the same ingredients.
    # Guard only explicit identity-changing modifiers or physical/preparation
    # states; leave semantic composition comparison to the optional classifier.
    for key in ("form", "preparation", "composition"):
        described = [catalog.tokens(item.get(key) or "") for item in (left, right)]
        conflict |= (described[0] ^ described[1]) & MODIFIERS
        group = FORM if key == "form" else PREPARATION if key == "preparation" else set()
        descriptors = [tokens & group for tokens in described]
        if all(descriptors) and descriptors[0] != descriptors[1]:
            conflict.add(key)
    if conflict:
        base.update(
            label="different_ingredient" if conflict & {"free", "without", "no", "non"} else "possible_substitution",
            decision_provenance="deterministic_guard",
            reason_codes=["identity_modifier_conflict"],
        )
    else:
        base["reason_codes"] = ["unresolved_identity"]
    return base


def _row(db, review_id, actor_id, *, lock=False):
    row = repository.get(db, review_id, lock=lock)
    if row is None or row.actor_id != actor_id:
        raise LookupError("Review unavailable")
    return row


def _fresh(db, row, *, lock=False, stock_snapshot=None, policy=None):
    try:
        source_changed = (
            digest(_snapshot(db, row.recipe_id, row.ingredient_id, lock=lock, stock_snapshot=stock_snapshot))
            != row.document["evidence_hash"]
        )
    except LookupError:
        source_changed = True
    return source_changed, (policy if policy is not None else culinary_service.review_policy(db)) != row.document[
        "policy"
    ]


def read(db, review_id, actor_id):
    row = _row(db, review_id, actor_id)
    doc = row.document
    source_changed, policy_changed = _fresh(db, row)
    expired = row.expires_at.replace(tzinfo=UTC) <= datetime.now(UTC)
    reasons = list(doc["reason_codes"])
    reasons += [
        reason
        for reason, yes in (
            ("source_changed", source_changed),
            ("policy_changed", policy_changed),
            ("review_expired", expired),
        )
        if yes
    ]
    stale = source_changed or policy_changed or expired
    results = copy.deepcopy(doc["results"])
    for result in results:
        result["can_confirm"] = bool(
            row.status == "pending"
            and not stale
            and result["label"] == "same_ingredient"
            and doc["snapshot"]["requested"]["canonical_ingredient"]
        )
    if stale:
        results = []
    return {
        "id": str(row.id),
        "recipe_id": str(row.recipe_id),
        "ingredient_id": str(row.ingredient_id),
        "requested_name": doc["snapshot"]["requested"]["name"],
        "requested_evidence": _public_evidence(doc["snapshot"]["requested"]),
        "requested_canonical_ingredient": doc["snapshot"]["requested"]["canonical_ingredient"],
        "revision": row.revision,
        "status": row.status,
        "evidence_hash": doc["evidence_hash"],
        "expires_at": row.expires_at,
        "results": results,
        "reason_codes": sorted(set(reasons)),
        "stale": stale,
        "ambiguous_tie": doc.get("ambiguous_tie", False),
        "provider_requests": doc["provider_requests"],
        "policy": doc["policy"],
    }


async def evaluate(db, command, actor_id, disconnected):
    snapshot = _snapshot(db, command.recipe_id, command.ingredient_id)
    policy = culinary_service.review_policy(db)
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    doc = {
        "snapshot": snapshot,
        "evidence_hash": digest(snapshot),
        "policy": policy,
        "results": [_result(candidate, snapshot["requested"]) for candidate in snapshot["candidates"]],
        "provider_requests": 0,
        "reason_codes": [] if snapshot["candidates"] else ["no_candidates"],
        "ambiguous_tie": False,
    }
    row = repository.create(
        db,
        id=uuid4(),
        recipe_id=command.recipe_id,
        ingredient_id=command.ingredient_id,
        actor_id=actor_id,
        created_at=now,
        expires_at=now + timedelta(minutes=15),
        revision=0,
        status="evaluating",
        token_hash=digest(token),
        document=copy.deepcopy(doc),
    )
    review_id, revision = row.id, 0
    failed = False
    for index, result in enumerate(doc["results"]):
        if result["reason_codes"] != ["unresolved_identity"]:
            continue
        if not policy["enabled"] or failed:
            result["reason_codes"] = ["review_disabled" if not policy["enabled"] else "not_evaluated_after_failure"]
            continue
        candidate = snapshot["candidates"][index]["item"]
        product, requested = _descriptor(candidate), _descriptor(snapshot["requested"])
        packet = {"product": product, "requested": requested}
        try:
            question = questions.request_for(packet)
        except ValueError:
            result["reason_codes"] = ["identity_evidence_truncated"]
            result["product_evidence"]["truncated"] = True
            continue
        result["reason_codes"] = []
        doc["provider_requests"] += 1
        repository.change(db, row, copy.deepcopy(doc), "evaluating", revision)
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
                    or any(_fresh(fresh, current))
                )

        try:
            answers = await culinary_service.evaluate_culinary(
                db, question, expected_policy=policy, stop_requested=stop_requested
            )
            result.update(questions.decode(answers, policy), decision_provenance="classifier")
        except ClassifierFailure as exc:
            result.update(label="insufficient_evidence", reason_codes=[exc.code], decision_provenance="abstention")
            failed = True
        except ValueError:
            result.update(
                label="insufficient_evidence", reason_codes=["invalid_response"], decision_provenance="abstention"
            )
            failed = True
        db.expire_all()
        row = _row(db, review_id, actor_id)
        if row.revision != revision:
            raise ValueError("Review changed during evaluation")
        if any(_fresh(db, row)) or row.expires_at.replace(tzinfo=UTC) <= datetime.now(UTC):
            doc["reason_codes"].append("source_changed_during_evaluation")
            failed = True
        repository.change(db, row, copy.deepcopy(doc), "evaluating", revision)
        revision += 1
    same = [
        result
        for result in doc["results"]
        if result["label"] == "same_ingredient" and result["decision_provenance"] == "classifier"
    ]
    same.sort(key=lambda result: -(result["probability"] or 0))
    doc["ambiguous_tie"] = any("ambiguous_choice_tie" in result["reason_codes"] for result in doc["results"]) or (
        len(same) > 1 and abs(same[0]["probability"] - same[1]["probability"]) < 1e-8
    )
    order = {"same_ingredient": 0, "possible_substitution": 1, "different_ingredient": 2, "insufficient_evidence": 3}
    doc["results"].sort(
        key=lambda result: (order[result["label"]], -(result["probability"] or 0), result["product_name"].casefold())
    )
    row = _row(db, review_id, actor_id)
    repository.change(db, row, doc, "interrupted" if failed else "pending", revision)
    return {**read(db, review_id, actor_id), "confirmation_token": token}


def _binding(db, review_id, command, actor_id, *, lock=False, policy=None, stock_snapshot=None):
    row = _row(db, review_id, actor_id, lock=lock)
    if (
        row.status != "pending"
        or row.revision != command.revision
        or not hmac.compare_digest(row.token_hash, digest(command.confirmation_token))
        or row.document["evidence_hash"] != command.evidence_hash
    ):
        raise ValueError("Review binding changed")
    if row.expires_at.replace(tzinfo=UTC) <= datetime.now(UTC) or any(
        _fresh(db, row, lock=lock, policy=policy, stock_snapshot=stock_snapshot)
    ):
        raise ValueError("Review changed or expired")
    return row


def confirm(db, review_id, command, actor_id):
    policy = culinary_service.review_policy(db, lock=True)
    stock_snapshot = stock.snapshot(db, lock=True)
    row = _binding(db, review_id, command, actor_id, lock=True, policy=policy, stock_snapshot=stock_snapshot)
    selected = next(
        (result for result in row.document["results"] if result["candidate_item_id"] == str(command.candidate_item_id)),
        None,
    )
    if selected is None or selected["label"] != "same_ingredient":
        raise ValueError("Only a selected same-ingredient decision can learn an alias")
    target = row.document["snapshot"]["requested"]
    if not target["canonical_ingredient"]:
        raise ValueError("Requested ingredient has no identity key")
    confirm_ingredient_item(
        db,
        ingredient_id=row.ingredient_id,
        item_id=command.candidate_item_id,
        expected_key=selected["canonical_ingredient"],
        expected_revision=selected["canonical_revision"],
        check_key=True,
        expected_ingredient_key=target["canonical_ingredient"],
        check_ingredient_key=True,
        replace_confirmed=command.replace_confirmed,
        commit=False,
    )
    doc = copy.deepcopy(row.document)
    doc["confirmation"] = {
        "actor_id": str(actor_id),
        "selected_item_id": str(command.candidate_item_id),
        "scope": "permanent_household_product_identity",
        "confirmed_at": datetime.now(UTC).isoformat(),
    }
    repository.change(db, row, doc, "confirmed", command.revision, commit=False)
    db.commit()
    db.expire_all()
    return read(db, review_id, actor_id)


def reject(db, review_id, command, actor_id):
    row = _binding(db, review_id, command, actor_id, lock=True)
    repository.change(db, row, copy.deepcopy(row.document), "rejected", command.revision)
    return read(db, review_id, actor_id)
