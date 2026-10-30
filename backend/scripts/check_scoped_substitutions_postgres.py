"""Guarded PostgreSQL scoped-swap approval and cook retry smoke in its own DB."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import uuid4


def check_cases(client, headers, session_factory, migrate):
    """Public API checks; all data belongs to the disposable synthetic database."""
    from decimal import Decimal
    from uuid import UUID

    from sqlalchemy import text

    def request(method, path, expected=200, **kwargs):
        response = getattr(client, method)(path, headers=headers, **kwargs)
        assert response.status_code == expected, f"{method} {path}: {response.status_code}: {response.text[:300]}"
        return response.json() if response.content else None

    def scalar(sql, **params):
        with session_factory() as db:
            if "id" in params:
                value = UUID(str(params["id"]))
                params["id"] = value.hex if db.get_bind().dialect.name == "sqlite" else value
            return db.execute(text(sql), params).scalar_one()

    def stock(item_id):
        return Decimal(str(scalar("SELECT COALESCE(sum(quantity),0) FROM inventory WHERE item_id=:id", id=item_id)))

    def globals_count():
        return scalar("SELECT count(*) FROM ingredient_substitutions")

    # Migration0033 seeds unapproved culinary suggestions. Preserve that real
    # baseline rather than assuming that an upgraded database has an empty table.
    baseline_globals = globals_count()
    with session_factory() as db:
        baseline_rows = list(db.execute(text(
            "SELECT * FROM ingredient_substitutions ORDER BY id"
        )).tuples())

    def unchanged_globals():
        with session_factory() as db:
            current = list(db.execute(text(
                "SELECT * FROM ingredient_substitutions ORDER BY id"
            )).tuples())
        return current == baseline_rows

    def item(name):
        return request("post", "/api/items", expected=201, json={
            "name": name, "stock_mode": "measured", "base_unit": "g",
        })["id"]

    def recipe(name, source, *, amount=10, unit="g"):
        value = request("post", "/api/recipes", json={
            "name": name, "instructions": "Mix the sauce. Serve the sauce.", "servings": 1,
            "ingredients": [{"item_id": source, "quantity": amount, "unit": unit}],
        })
        detail = request("get", f"/api/recipes/{value['id']}")
        return value["id"], detail["ingredients"][0]["ingredient_id"]

    def start(recipe_id):
        return request("post", "/api/cook-sessions", expected=201, json={"recipe_id": recipe_id})["id"]

    context = {"cooking_role": "seasoning", "preparation": "cold sauce",
               "restrictions": "No stated restrictions; ingredients checked manually"}

    def review(session_id, ingredient_id, target):
        return request("post", f"/api/cook-sessions/{session_id}/substitution-reviews", json={
            "ingredient_id": ingredient_id, "substitute_item_id": target, "context": context,
        })

    def approve(row, **changes):
        return request("post", f"/api/substitution-reviews/{row['id']}/approve", json={
            "expected_binding": row["binding"], "confirmed": True, "ratio": "1.25", "unit": "g", **changes,
        })

    original = item("Synthetic original chipotle")
    target = item("Synthetic smoked chipotle spice")
    location = request("post", "/api/locations", expected=201, json={"name": "Synthetic scoped swap pantry"})["id"]
    response = client.post("/api/inventory/add", headers=headers, json={
        "item_id": target, "location_id": location, "quantity": 100, "unit": "g",
    })
    assert response.status_code in (200, 201)
    recipe_id, ingredient_id = recipe("Synthetic one-cook sauce", original)
    session_id = start(recipe_id)
    before = stock(target)
    row = review(session_id, ingredient_id, target)
    assert row["from_unit"] == "g" and row["evaluation"] is None
    assert stock(target) == before and globals_count() == baseline_globals and unchanged_globals()
    approved = approve(row)
    assert approved["scope"] == "one_use"
    assert stock(target) == before and globals_count() == baseline_globals and unchanged_globals()
    request("post", f"/api/cook-sessions/{session_id}/step", json={"action": "next"})
    finished = request("post", f"/api/cook-sessions/{session_id}/finish", json={})
    event = finished["cook"]["cook_event_id"]
    assert stock(target) == before - Decimal("12.5") and globals_count() == baseline_globals and unchanged_globals()
    history = request("get", f"/api/cook-sessions/{session_id}/substitution-reviews")["reviews"]
    assert next(entry for entry in history if entry["id"] == row["id"])["consumed_at"] is not None
    again = request("post", f"/api/cook-sessions/{session_id}/finish", json={})
    assert again["cook"]["cook_event_id"] == event and stock(target) == before - Decimal("12.5")
    # A later catalog edit cannot turn an already-finished retry into a new cook.
    request("put", f"/api/items/{target}", json={"name": "Synthetic smoked chipotle renamed"})
    again = request("post", f"/api/cook-sessions/{session_id}/finish", json={})
    assert again["cook"]["cook_event_id"] == event and stock(target) == before - Decimal("12.5")

    # Explicit recipe-only reuse is local to the same recipe/ingredient revision.
    recipe_session = start(recipe_id)
    recipe_review = approve(review(recipe_session, ingredient_id, target), scope="recipe")
    next_session = start(recipe_id)
    choices = request("get", f"/api/cook-sessions/{next_session}/substitution-reviews")
    assert recipe_review["id"] in {choice["id"] for choice in choices["recipe_approvals"]}
    reusable = next(choice for choice in choices["recipe_approvals"] if choice["id"] == recipe_review["id"])
    request("post", f"/api/cook-sessions/{next_session}/substitution-reviews/{recipe_review['id']}/attach", json={
        "expected_binding": reusable["binding"], "confirmed": True,
    })
    assert globals_count() == baseline_globals and unchanged_globals()
    other_recipe, _ = recipe("Synthetic unrelated sauce", original)
    other_session = start(other_recipe)
    response = client.post(
        f"/api/cook-sessions/{other_session}/substitution-reviews/{recipe_review['id']}/attach",
        headers=headers, json={"expected_binding": recipe_review["binding"], "confirmed": True},
    )
    assert response.status_code in (400, 409) and globals_count() == baseline_globals and unchanged_globals()

    # Real PostgreSQL contention reproduces the reviewed lock-order cycle:
    # finish holds the recipe while another active cook reuses review B. The
    # resolver must lock all reviews before the recipe, so neither waits in a
    # cycle. Timing hooks wrap real queries; no database behavior is mocked.
    with session_factory() as db:
        postgres = db.get_bind().dialect.name == "postgresql"
    if postgres:
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier, Event
        from unittest.mock import patch

        from modules.planning.services import substitution_reviews as reviews

        extra_source = item("Synthetic second seasoning")
        extra_target = item("Synthetic second replacement")
        response = client.post("/api/inventory/add", headers=headers, json={
            "item_id": extra_target, "location_id": location, "quantity": 20, "unit": "g",
        })
        assert response.status_code in (200, 201)
        paired = request("post", "/api/recipes", json={
            "name": "Synthetic concurrent multi-swap sauce", "servings": 1, "instructions": "Mix and serve.",
            "ingredients": [{"item_id": original, "quantity": 10, "unit": "g"},
                            {"item_id": extra_source, "quantity": 2, "unit": "g"}],
        })["id"]
        lines = request("get", f"/api/recipes/{paired}")["ingredients"]
        by_item = {line["item_id"]: line["ingredient_id"] for line in lines}
        first_session = start(paired)
        approve(review(first_session, by_item[original], target))
        shared = approve(review(first_session, by_item[extra_source], extra_target), scope="recipe")
        claimed, allow_resolve = Event(), Event()
        ready, release, attach_entered, attach_acquired = Event(), Event(), Event(), Event()
        original_resolve = reviews.resolve_attached
        original_lock = reviews.repository.lock_recipe
        original_review = reviews.repository.review

        def resolve_tagged(db, session, *, lock=False):
            if lock and str(session.id) == first_session:
                db.info["scope_ci_finish"] = True
                # Finish has already claimed this session. Create the second
                # active session before taking the recipe lock: inserting its
                # recipe FK legitimately needs PostgreSQL FOR KEY SHARE.
                claimed.set()
                assert allow_resolve.wait(5), "Timed out creating the reuse cook before recipe locking"
            try:
                return original_resolve(db, session, lock=lock)
            finally:
                db.info.pop("scope_ci_finish", None)

        def lock_and_pause(db, value):
            attaching = not db.info.get("scope_ci_finish") and str(value) == paired
            if attaching:
                attach_entered.set()
            result = original_lock(db, value)
            if attaching:
                attach_acquired.set()
            if db.info.get("scope_ci_finish") and not db.info.get("scope_ci_paused"):
                db.info["scope_ci_paused"] = True
                ready.set()
                assert release.wait(5), "Timed out scheduling the scoped-swap race"
            return result

        def observed_review(db, review_id, *, lock=False):
            attaching = lock and str(review_id) == shared["id"] and not db.info.get("scope_ci_finish")
            if attaching:
                attach_entered.set()
            result = original_review(db, review_id, lock=lock)
            if attaching:
                attach_acquired.set()
            return result

        before_race = stock(target)
        with patch.object(reviews, "resolve_attached", resolve_tagged), patch.object(
            reviews.repository, "lock_recipe", lock_and_pause,
        ), patch.object(
            reviews.repository, "review", observed_review,
        ), ThreadPoolExecutor(max_workers=2) as pool:
            finish_future = pool.submit(request, "post", f"/api/cook-sessions/{first_session}/finish", json={})
            try:
                assert claimed.wait(5), "Finish did not claim the first cook"
                second_session = start(paired)
                reusable_rows = request("get", f"/api/cook-sessions/{second_session}/substitution-reviews")
                reuse = next(choice for choice in reusable_rows["recipe_approvals"] if choice["id"] == shared["id"])
                allow_resolve.set()
                assert ready.wait(5), "Finish did not reach the real recipe lock"
                attach_future = pool.submit(
                    request, "post", f"/api/cook-sessions/{second_session}/substitution-reviews/{shared['id']}/attach",
                    json={"expected_binding": reuse["binding"], "confirmed": True},
                )
                # Both old and corrected order contend here; only the old order
                # forms a cycle once Finish is released toward the next review.
                assert attach_entered.wait(5), "Reuse did not attempt its real recipe/review lock"
                attach_acquired.wait(0.5)
                assert not attach_future.done()
            finally:
                allow_resolve.set()
                release.set()
            assert finish_future.result(timeout=15)["session"]["status"] == "finished"
            assert attach_future.result(timeout=15)["scope"] == "recipe"
        assert stock(target) == before_race - Decimal("12.5")
        assert stock(extra_target) == Decimal("17.5") and globals_count() == baseline_globals and unchanged_globals()

        # Different recipes do not share a recipe lock. Pair-wise sorting alone
        # is insufficient: Finish (A,C), then (B,D), can hold C and wait for B
        # while another recipe's approval (B,C) holds B and waits for C.
        # Stable synthetic review IDs make the adversarial pair order explicit.
        # Numeric suffixes canonicalize to one ingredient and legitimately share
        # a FEFO pool. This lock-order fixture requires independent stock pools.
        union_items = [item(f"Synthetic union-lock item {suffix}")
                       for suffix in ("alpha", "beta", "gamma", "delta")]
        assert len({request("get", f"/api/items/{value}")["canonical_ingredient"]
                    for value in union_items}) == 4
        a, b, c, d = sorted(union_items)
        for stocked in (c, d):
            response = client.post("/api/inventory/add", headers=headers, json={
                "item_id": stocked, "location_id": location, "quantity": 20, "unit": "g",
            })
            assert response.status_code in (200, 201)
        union_recipe = request("post", "/api/recipes", json={
            "name": "Synthetic union-lock finish", "servings": 1, "instructions": "Mix and serve.",
            "ingredients": [{"item_id": a, "quantity": 2, "unit": "g"},
                            {"item_id": b, "quantity": 2, "unit": "g"}],
        })["id"]
        union_lines = {line["item_id"]: line["ingredient_id"]
                       for line in request("get", f"/api/recipes/{union_recipe}")["ingredients"]}
        union_session = start(union_recipe)

        def ordered_draft(source, destination, suffix):
            draft = review(union_session, union_lines[source], destination)
            fixed_id = UUID(f"00000000-0000-4000-8000-{suffix:012d}")
            with session_factory() as db:
                db.execute(text("UPDATE substitution_reviews SET id=:fixed WHERE id=:old"),
                           {"fixed": fixed_id, "old": UUID(draft["id"])})
                db.commit()
            draft["id"] = str(fixed_id)
            return draft

        approve(ordered_draft(a, c, 101))
        approve(ordered_draft(b, d, 102))
        competing_recipe, competing_line = recipe("Synthetic different-recipe approval", b, amount=2)
        union_claimed, union_allow = Event(), Event()
        union_ready, union_release = Event(), Event()
        competitor_entered, competitor_acquired = Event(), Event()
        real_evidence = reviews.item_evidence

        def union_tagged(db, session, *, lock=False):
            if lock and str(session.id) == union_session:
                db.info["scope_ci_union_finish"] = True
                # A new active cook would abandon an active predecessor. Wait
                # until Finish has committed its claim, but create the other
                # cook/draft before holding any item or recipe FK parent locks.
                union_claimed.set()
                assert union_allow.wait(5), "Timed out creating the other cook after the finish claim"
            try:
                return original_resolve(db, session, lock=lock)
            finally:
                db.info.pop("scope_ci_union_finish", None)

        def observed_evidence(db, item_id, *, lock=False):
            finish = bool(db.info.get("scope_ci_union_finish"))
            competes = lock and str(item_id) == b and not finish
            if competes:
                competitor_entered.set()
            result = real_evidence(db, item_id, lock=lock)
            if competes:
                competitor_acquired.set()
            if lock and finish and str(item_id) == c and not db.info.get("scope_ci_union_paused"):
                db.info["scope_ci_union_paused"] = True
                union_ready.set()
                assert union_release.wait(5), "Timed out scheduling different-recipe item contention"
            return result

        union_before_events = scalar("SELECT count(*) FROM recipe_cook_events")
        with patch.object(reviews, "resolve_attached", union_tagged), patch.object(
            reviews, "item_evidence", observed_evidence,
        ), ThreadPoolExecutor(max_workers=2) as pool:
            union_finish = pool.submit(request, "post", f"/api/cook-sessions/{union_session}/finish", json={})
            try:
                assert union_claimed.wait(5), "Finish did not claim the union-lock cook"
                # A finishing cook must not be deleted between stock commits
                # and its final receipt. The parent guard must roll back cleanly.
                request("delete", f"/api/recipes/{union_recipe}", expected=409)
                assert request("get", f"/api/recipes/{union_recipe}")["recipe"]["id"] == union_recipe
                competing_session = start(competing_recipe)
                competing = review(competing_session, competing_line, c)
                # Draft creation also reads B under lock; only the subsequent
                # approval worker should satisfy the contention observations.
                competitor_entered.clear()
                competitor_acquired.clear()
                union_allow.set()
                assert union_ready.wait(5), "Finish did not lock item C"
                competing_approval = pool.submit(approve, competing)
                assert competitor_entered.wait(5), "Other recipe did not attempt item B"
                competitor_acquired.wait(0.5)
                assert not competing_approval.done()
            finally:
                union_allow.set()
                union_release.set()
            assert union_finish.result(timeout=15)["session"]["status"] == "finished"
            assert competing_approval.result(timeout=15)["status"] == "approved"
        assert stock(c) == Decimal("17.5") and stock(d) == Decimal("17.5")
        assert scalar("SELECT count(*) FROM recipe_cook_events") == union_before_events + 1
        assert globals_count() == baseline_globals and unchanged_globals()

        # Whole-recipe deletion cascades sessions as well as reviews. A paused
        # approval already owns its Session; deletion must fail NOWAIT/409 and
        # roll back its Recipe lock, rather than wait Session -> Recipe -> Session.
        deletion_recipe, deletion_line = recipe("Synthetic busy recipe deletion", original)
        deletion_session = start(deletion_recipe)
        deletion_draft = review(deletion_session, deletion_line, target)
        deletion_ready, deletion_release = Event(), Event()
        real_session = reviews.repository.session

        def deletion_session_lock(db, session_id, *, lock=False):
            value = real_session(db, session_id, lock=lock)
            if lock and str(session_id) == deletion_session:
                db.info["scope_ci_delete_approval"] = True
            return value

        def deletion_parent_lock(db, value):
            if db.info.get("scope_ci_delete_approval") and not db.info.get("scope_ci_delete_paused"):
                db.info["scope_ci_delete_paused"] = True
                deletion_ready.set()
                assert deletion_release.wait(5), "Timed out scheduling busy whole-recipe deletion"
            return original_lock(db, value)

        deletion_before_stock = stock(target)
        with patch.object(reviews.repository, "session", deletion_session_lock), patch.object(
            reviews.repository, "lock_recipe", deletion_parent_lock,
        ), ThreadPoolExecutor(max_workers=1) as pool:
            pending_approval = pool.submit(approve, deletion_draft)
            try:
                assert deletion_ready.wait(5), "Approval did not hold its real session lock"
                request("delete", f"/api/recipes/{deletion_recipe}", expected=409)
                assert request("get", f"/api/recipes/{deletion_recipe}")["recipe"]["id"] == deletion_recipe
            finally:
                deletion_release.set()
            assert pending_approval.result(timeout=15)["status"] == "approved"
        assert stock(target) == deletion_before_stock
        assert globals_count() == baseline_globals and unchanged_globals()

        # Ingredient replacement's real FK cascade must serialize with Finish.
        # Pause Finish before its parent lock, let the edit win, then require a
        # controlled stale-choice response with no deduction. The old order
        # holds a review here and makes DELETE wait on that same child row.
        removal_recipe, removal_line = recipe("Synthetic concurrent ingredient edit", original)
        removal_source = item("Synthetic replacement original spice")
        removal_session = start(removal_recipe)
        removal_review = approve(review(removal_session, removal_line, target))
        removal_ready, removal_release = Event(), Event()
        removal_before_stock = stock(target)
        removal_before_events = scalar("SELECT count(*) FROM recipe_cook_events")

        def removal_tagged(db, session, *, lock=False):
            if lock and str(session.id) == removal_session:
                db.info["scope_ci_removal_finish"] = True
            try:
                return original_resolve(db, session, lock=lock)
            finally:
                db.info.pop("scope_ci_removal_finish", None)

        def removal_parent_lock(db, value):
            if db.info.get("scope_ci_removal_finish") and not db.info.get("scope_ci_removal_paused"):
                db.info["scope_ci_removal_paused"] = True
                removal_ready.set()
                assert removal_release.wait(5), "Timed out scheduling ingredient replacement"
            return original_lock(db, value)

        with patch.object(reviews, "resolve_attached", removal_tagged), patch.object(
            reviews.repository, "lock_recipe", removal_parent_lock,
        ), ThreadPoolExecutor(max_workers=2) as pool:
            removal_finish = pool.submit(client.post, f"/api/cook-sessions/{removal_session}/finish",
                                         headers=headers, json={})
            try:
                assert removal_ready.wait(5), "Finish did not reach its parent recipe lock"
                removal_edit = pool.submit(request, "put", f"/api/recipes/{removal_recipe}/ingredients", json=[{
                    "item_id": removal_source, "quantity_required": 10, "unit": "g",
                }])
                # Recipe-first Finish holds no child review before this pause,
                # so the real edit/cascade must complete before it is released.
                replaced_rows = removal_edit.result(timeout=2)
                assert replaced_rows["ingredients"][0]["ingredient_id"] != removal_line
            finally:
                removal_release.set()
            stale_finish = removal_finish.result(timeout=15)
            assert stale_finish.status_code in (400, 409), stale_finish.text[:300]
        assert stock(target) == removal_before_stock
        assert scalar("SELECT count(*) FROM recipe_cook_events") == removal_before_events
        remaining = request("get", f"/api/cook-sessions/{removal_session}/substitution-reviews")
        assert removal_review["id"] in remaining["missing_attachment_ids"]
        assert globals_count() == baseline_globals and unchanged_globals()

        duplicate_session = start(recipe_id)
        approve(review(duplicate_session, ingredient_id, target))
        before_duplicate = stock(target)
        before_events = scalar("SELECT count(*) FROM recipe_cook_events")
        barrier = Barrier(2)

        def finish_together():
            barrier.wait(timeout=5)
            return client.post(f"/api/cook-sessions/{duplicate_session}/finish", headers=headers, json={})

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(finish_together) for _ in range(2)]
            responses = [future.result(timeout=15) for future in futures]
        assert all(response.status_code in (200, 400, 409) for response in responses)
        assert any(response.status_code == 200 for response in responses)
        request("post", f"/api/cook-sessions/{duplicate_session}/finish", json={})
        assert stock(target) == before_duplicate - Decimal("12.5")
        assert scalar("SELECT count(*) FROM recipe_cook_events") == before_events + 1
        assert globals_count() == baseline_globals and unchanged_globals()

    # Ingredient replacement really cascades review deletion on PostgreSQL.
    # Keep the remaining plan ID visible/removable; never silently discard the
    # person's choice or guess an identity after its source metadata is gone.
    edited_source = item("Synthetic edited source ingredient")
    edited_recipe, old_line = recipe("Synthetic changed-ingredient recovery", original)
    edited_session = start(edited_recipe)
    old_approval = approve(review(edited_session, old_line, target))
    before_edit_stock = stock(target)
    replaced = request("put", f"/api/recipes/{edited_recipe}/ingredients", json=[{
        "item_id": edited_source, "quantity_required": 10, "unit": "g",
    }])
    new_line = replaced["ingredients"][0]["ingredient_id"]
    assert new_line != old_line
    assert scalar("SELECT count(*) FROM substitution_reviews WHERE id=:id", id=old_approval["id"]) == 0
    broken = request("get", f"/api/cook-sessions/{edited_session}/substitution-reviews")
    assert old_approval["id"] in broken["missing_attachment_ids"]
    request("patch", f"/api/cook-sessions/{edited_session}", json={"plan": {"substitution_approval_ids": []}})
    cleaned = request("get", f"/api/cook-sessions/{edited_session}/substitution-reviews")
    assert cleaned["missing_attachment_ids"] == [] and cleaned["attached_ids"] == []
    corrected = approve(review(edited_session, new_line, target))
    current = request("get", f"/api/cook-sessions/{edited_session}/substitution-reviews")
    assert corrected["id"] in current["attached_ids"] and current["missing_attachment_ids"] == []
    assert stock(target) == before_edit_stock and globals_count() == baseline_globals and unchanged_globals()

    # New global rows carry their ratio's original unit; legacy rows retain NULL.
    global_source = item("Synthetic global original")
    global_target = item("Synthetic global replacement")
    global_recipe, global_line = recipe("Synthetic global basis", global_source, amount=1)
    global_session = start(global_recipe)
    global_row = review(global_session, global_line, global_target)
    response = client.post(f"/api/substitution-reviews/{global_row['id']}/approve", headers=headers, json={
        "expected_binding": global_row["binding"], "confirmed": True, "ratio": "2", "unit": "g", "scope": "global",
    })
    assert response.status_code in (400, 409, 422) and globals_count() == baseline_globals and unchanged_globals()
    approve(global_row, scope="global", permanent_ack=True, ratio="2")
    new_global = request("get", f"/api/substitutions/for/{global_source}")["substitutions"][0]
    assert new_global["from_unit"] == "g"
    assert bool(scalar("SELECT approved FROM ingredient_substitutions WHERE id=:id", id=new_global["id"]))
    legacy_source = item("Synthetic legacy original")
    legacy_target = item("Synthetic legacy replacement")
    legacy = request("post", "/api/substitutions", expected=201, json={
        "from_item_id": legacy_source, "to_item_id": legacy_target, "ratio": 3, "unit": "g",
    })
    assert legacy["from_unit"] is None
    assert bool(scalar("SELECT approved FROM ingredient_substitutions WHERE id=:id", id=legacy["id"]))
    migrate("0066_recipe_import_review", downgrade=True)
    assert bool(scalar("SELECT approved FROM ingredient_substitutions WHERE id=:id", id=legacy["id"])) is True
    assert bool(scalar("SELECT approved FROM ingredient_substitutions WHERE id=:id", id=new_global["id"])) is False
    assert scalar("SELECT status FROM cook_sessions WHERE id=:id", id=global_session) == "abandoned"
    migrate("head")
    assert scalar("SELECT ratio FROM ingredient_substitutions WHERE id=:id", id=legacy["id"]) == Decimal("3")
    assert bool(scalar("SELECT approved FROM ingredient_substitutions WHERE id=:id", id=legacy["id"])) is True
    assert scalar("SELECT from_unit FROM ingredient_substitutions WHERE id=:id", id=legacy["id"]) is None
    assert bool(scalar("SELECT approved FROM ingredient_substitutions WHERE id=:id", id=new_global["id"])) is False
    assert request("get", f"/api/cook-sessions/{global_session}")["status"] == "abandoned"
    after_rollback_stock = stock(target)
    historical = request("post", f"/api/cook-sessions/{session_id}/finish", json={})
    assert historical["cook"]["cook_event_id"] == event and stock(target) == after_rollback_stock
    assert scalar("SELECT count(*) FROM classifier_evaluations") == 0
    sys.stdout.write(
        "Scoped substitution smoke: manual approval, no global/stock authority, step-safe binding, "
        "one-use deduction/consumption/retry, historical retry after catalog change, explicit recipe reuse/isolation, "
        "deleted-ingredient attachment recovery, global unit basis and safe legacy/new-global/active-cook downgrade passed; "
        + ("PostgreSQL shared-review/different-recipe item-lock/edit-cascade contention and double-finish passed; " if postgres
           else "PostgreSQL-only lock/double-finish cases pending remote CI; ")
        + "0 provider requests.\n"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-ci-database", action="store_true")
    args = parser.parse_args()
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.engine import make_url

    base_url = make_url(os.environ.get("DATABASE_URL", "sqlite://"))
    if not (args.confirm_ci_database and os.environ.get("CI") == "true"
            and base_url.drivername == "postgresql+psycopg" and base_url.host in {"localhost", "127.0.0.1"}
            and base_url.username == "pantryos_ci" and base_url.database == "pantryos"):
        parser.error("Requires acknowledgement and the disposable local CI PostgreSQL database")
    name = "pantryos_scope_ci_" + uuid4().hex
    if not re.fullmatch(r"pantryos_scope_ci_[a-f0-9]{32}", name):
        raise RuntimeError("Invalid generated CI database name")
    created_name = None
    admin_engine = create_engine(base_url, isolation_level="AUTOCOMMIT",
                                 connect_args={"options": "-c statement_timeout=30000 -c lock_timeout=5000"})
    app_engine = None
    client = None
    original_cwd = os.getcwd()
    isolated_cwd = tempfile.TemporaryDirectory(prefix="pantryos-scoped-swap-ci-")
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
            created_name = name
        os.environ["DATABASE_URL"] = base_url.set(database=name).render_as_string(hide_password=False)
        from cryptography.fernet import Fernet

        os.environ["CONFIG_SECRET_KEY"] = Fernet.generate_key().decode()
        backend = Path(__file__).resolve().parents[1]

        def migrate(revision, *, downgrade=False):
            source = (
                "import sys; from alembic import command; from alembic.config import Config; "
                "sys.path.insert(0, sys.argv[1]); c=Config(sys.argv[2]); "
                "c.set_main_option('script_location', sys.argv[3]); "
                "getattr(command,sys.argv[4])(c,sys.argv[5])"
            )
            subprocess.run([sys.executable, "-c", source, str(backend), str(backend / "alembic.ini"),
                            str(backend / "migrations"), "downgrade" if downgrade else "upgrade", revision],
                           cwd=isolated_cwd.name, check=True, timeout=120)

        migrate("head")
        os.chdir(isolated_cwd.name)  # Never load a workspace .env.
        sys.path.insert(0, str(backend))
        from fastapi.testclient import TestClient

        from app.db.session import SessionLocal, engine
        from app.main import app

        app_engine = engine

        @event.listens_for(engine, "connect")
        def timeouts(connection, _record):
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '5000ms'")
                cursor.execute("SET lock_timeout = '3000ms'")

        engine.dispose()
        client = TestClient(app)
        credentials = {"email": "synthetic-scope-ci@example.com", "password": "SyntheticScopeCi987!"}
        assert client.post("/api/auth/bootstrap-admin", json=credentials).status_code in (200, 201)
        login = client.post("/api/auth/login", json=credentials)
        assert login.status_code == 200
        check_cases(client, {"Authorization": "Bearer " + login.json()["token"]}, SessionLocal, migrate)
    finally:
        if client is not None:
            client.close()
        if app_engine is not None:
            app_engine.dispose()
        if created_name is not None:
            if created_name != name or created_name == "pantryos" or not re.fullmatch(r"pantryos_scope_ci_[a-f0-9]{32}", created_name):
                raise RuntimeError("Refusing to drop an unexpected database")
            with admin_engine.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{created_name}" WITH (FORCE)'))
        admin_engine.dispose()
        os.chdir(original_cwd)
        isolated_cwd.cleanup()


if __name__ == "__main__":
    main()
