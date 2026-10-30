"""Server-side cook sessions — ADR 0026 §6.

Pinned here:
  * the step splitter — the only one; the cook-along page shows the
    session's steps (ADR 0026 §6.1);
  * moving through steps never touches stock — only Finish does, through the
    ordinary cook path with ADR 0022's absolute ``servings_made``;
  * one active session at a time, and ending one early costs nothing.
"""

from __future__ import annotations

from decimal import Decimal

import httpx

from modules.planning.services.steps import split_instructions

# --- the splitter -------------------------------------------------------------


def test_splits_numbered_lines_and_strips_markers() -> None:
    assert split_instructions("1. Brown the beef.\n2. Add onions.\n3. Simmer.") == [
        "Brown the beef.",
        "Add onions.",
        "Simmer.",
    ]
    assert split_instructions("1) Mix.\n2) Bake.") == ["Mix.", "Bake."]
    assert split_instructions("Step 1: Mix.\nStep 2: Bake.") == ["Mix.", "Bake."]


def test_never_splits_mid_line_or_on_sentences() -> None:
    assert split_instructions("Bake at 350 for 1.5 hours. Remove when golden.") == [
        "Bake at 350 for 1.5 hours. Remove when golden."
    ]
    assert split_instructions("Add 1. 5 cups of flour") == ["Add 1. 5 cups of flour"]
    prose = "Heat the oil. Add the garlic. Cook until fragrant."
    assert split_instructions(prose) == [prose]


def test_paragraph_and_newline_fallbacks() -> None:
    assert split_instructions("Brown the beef.\n\nAdd onions and cook.\n\nSimmer.") == [
        "Brown the beef.",
        "Add onions and cook.",
        "Simmer.",
    ]
    assert split_instructions("Brown the beef\nuntil dark.\n\nSimmer.") == ["Brown the beef until dark.", "Simmer."]
    assert split_instructions("1. Brown the beef\nuntil well seared.\n2. Add onions.") == [
        "Brown the beef until well seared.",
        "Add onions.",
    ]
    assert split_instructions("Mix.\nBake.\nServe.") == ["Mix.", "Bake.", "Serve."]


def test_sequence_rule_keeps_ingredient_lines_out() -> None:
    raw = "\n".join(
        [
            "1 Peel and mince shallot. Peel and mince the garlic.",
            "2 Add shallot, garlic, fish sauce, maple syrup, and pepper to a medium bowl; whisk together.",
            "½ tsp black pepper",
            "3 Cut the steak into 3-inch sections, then thinly slice each section against the grain;",
            "add to the bowl with the marinade. Turn to coat and set aside to marinate.",
            "1 ½ lb striploin (New York strip) steak",
            "4 Wash and dry the fresh produce.",
        ]
    )
    steps = split_instructions(raw)
    assert len(steps) == 4
    assert steps[0] == "Peel and mince shallot. Peel and mince the garlic."
    assert "Cut the steak" in steps[2] and "Turn to coat" in steps[2]
    assert steps[3] == "Wash and dry the fresh produce."

    repeated = "\n".join(
        [
            "1 Preheat a large skillet over medium-high heat.",
            "2 Wash and dry the fresh produce.",
            "2 (1 inch) pieces ginger root",
            "3 Once the skillet is hot, add oil and swirl to coat the bottom.",
        ]
    )
    assert split_instructions(repeated)[2] == "Once the skillet is hot, add oil and swirl to coat the bottom."


def test_empty_instructions_have_no_steps() -> None:
    assert split_instructions(None) == []
    assert split_instructions("   \n  ") == []


# --- sessions -----------------------------------------------------------------


def _admin_headers(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    login = client.post("/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['token']}"}


def _setup(client: httpx.Client, h: dict) -> tuple[str, str, str]:
    """A 4-serving flatbread: 8 g flour total, 100 g on hand, three steps."""
    loc = client.post("/api/locations", json={"name": "Pantry"}, headers=h).json()["id"]
    item = client.post("/api/items", json={"name": "Flour", "stock_mode": "measured", "size_unit": "g"}, headers=h).json()
    client.post(
        "/api/inventory/add",
        json={"item_id": item["id"], "location_id": loc, "quantity": 100, "unit": "g"},
        headers=h,
    )
    recipe = client.post(
        "/api/recipes",
        json={
            "name": "Flatbread",
            "servings": 4,
            "instructions": "1. Mix the flour with water.\n2. Rest ten minutes.\n3. Bake.",
            "ingredients": [{"name": "Flour", "quantity": 8, "unit": "g"}],
        },
        headers=h,
    ).json()
    return loc, item["id"], recipe["id"]


def _on_hand(client: httpx.Client, h: dict, loc: str, item_id: str) -> Decimal:
    rows = client.get(f"/api/inventory?location_id={loc}", headers=h).json()["items"]
    row = next((r for r in rows if r["item_id"] == item_id), None)
    return Decimal(str(row["quantity"])) if row else Decimal("0")


def test_walks_steps_without_touching_stock(client: httpx.Client) -> None:
    h = _admin_headers(client)
    loc, item_id, recipe_id = _setup(client, h)

    started = client.post("/api/cook-sessions", json={"recipe_id": recipe_id, "servings_made": 2}, headers=h)
    assert started.status_code == 201, started.text
    body = started.json()
    assert (body["step_number"], body["total_steps"]) == (1, 3)
    assert body["step_text"] == "Mix the flour with water."
    assert body["is_first_step"] and not body["is_last_step"]
    # Scaled to 2 of 4 servings: half of 8 g. And step 1 mentions the flour.
    assert body["ingredients"][0]["quantity"] == "4"
    assert [i["item"] for i in body["step_ingredients"]] == ["Flour"]

    sid = body["id"]
    nxt = client.post(f"/api/cook-sessions/{sid}/step", json={"action": "next"}, headers=h).json()
    assert nxt["step_number"] == 2 and nxt["step_ingredients"] == []
    last = client.post(f"/api/cook-sessions/{sid}/step", json={"action": "goto", "step": 3}, headers=h).json()
    assert last["is_last_step"]
    # "next" at the end stays at the end rather than erroring.
    assert client.post(f"/api/cook-sessions/{sid}/step", json={"action": "next"}, headers=h).json()["step_number"] == 3
    back = client.post(f"/api/cook-sessions/{sid}/step", json={"action": "previous"}, headers=h).json()
    assert back["step_number"] == 2
    bad = client.post(f"/api/cook-sessions/{sid}/step", json={"action": "goto", "step": 9}, headers=h)
    assert bad.status_code == 400

    assert _on_hand(client, h, loc, item_id) == Decimal("100")
    assert client.get("/api/cook-sessions/active", headers=h).json()["id"] == sid


def test_finish_deducts_scaled_amount_once(client: httpx.Client) -> None:
    h = _admin_headers(client)
    loc, item_id, recipe_id = _setup(client, h)
    sid = client.post("/api/cook-sessions", json={"recipe_id": recipe_id, "servings_made": 2}, headers=h).json()["id"]

    done = client.post(f"/api/cook-sessions/{sid}/finish", json={"location_id": loc}, headers=h)
    assert done.status_code == 200, done.text
    assert done.json()["session"]["status"] == "finished"
    assert done.json()["session"]["cook_event_id"] == done.json()["cook"]["cook_event_id"]
    assert _on_hand(client, h, loc, item_id) == Decimal("96")  # 100 - 4 g

    # Finishing again (a retry after a lost response) answers with the cook
    # already recorded and deducts nothing more; stepping is refused.
    again = client.post(f"/api/cook-sessions/{sid}/finish", json={"location_id": loc}, headers=h)
    assert again.status_code == 200, again.text
    # ...and says what the first Finish said: the batch size, the trimmed lines.
    assert again.json()["cook"] == {**done.json()["cook"], "already_finished": True}
    assert again.json()["cook"]["scale_used"] == done.json()["cook"]["scale_used"]
    assert _on_hand(client, h, loc, item_id) == Decimal("96")
    assert client.post(f"/api/cook-sessions/{sid}/step", json={"action": "next"}, headers=h).status_code == 400
    assert client.get("/api/cook-sessions/active", headers=h).status_code == 204


def test_starting_again_abandons_the_old_session_and_abandoning_is_free(client: httpx.Client) -> None:
    h = _admin_headers(client)
    loc, item_id, recipe_id = _setup(client, h)
    first = client.post("/api/cook-sessions", json={"recipe_id": recipe_id}, headers=h).json()["id"]
    second = client.post("/api/cook-sessions", json={"recipe_id": recipe_id}, headers=h).json()["id"]

    assert client.get(f"/api/cook-sessions/{first}", headers=h).json()["status"] == "abandoned"
    assert client.get("/api/cook-sessions/active", headers=h).json()["id"] == second

    stopped = client.delete(f"/api/cook-sessions/{second}", headers=h)
    assert stopped.status_code == 200 and stopped.json()["status"] == "abandoned"
    assert client.get("/api/cook-sessions/active", headers=h).status_code == 204
    assert _on_hand(client, h, loc, item_id) == Decimal("100")


def test_unknown_recipe_is_404(client: httpx.Client) -> None:
    h = _admin_headers(client)
    resp = client.post(
        "/api/cook-sessions", json={"recipe_id": "00000000-0000-0000-0000-000000000000"}, headers=h
    )
    assert resp.status_code == 404


def test_only_one_overlapping_finish_cooks(client: httpx.Client) -> None:
    """Two finishes that both saw the session active: the claim lets one cook."""
    from app.db.session import SessionLocal
    from modules.planning.services import cook_sessions

    h = _admin_headers(client)
    loc, item_id, recipe_id = _setup(client, h)
    sid = client.post("/api/cook-sessions", json={"recipe_id": recipe_id, "servings_made": 2}, headers=h).json()["id"]

    from uuid import UUID

    with SessionLocal() as first, SessionLocal() as second:
        # Both requests load the session while it is still active...
        cook_sessions.get_session(first, UUID(sid))
        cook_sessions.get_session(second, UUID(sid))
        # ...the first claims and cooks; the second finds it already finished.
        cook_sessions.finish_session(first, session_id=UUID(sid), location_id=UUID(loc))
        _, result = cook_sessions.finish_session(second, session_id=UUID(sid), location_id=UUID(loc))
    assert result["already_finished"] is True
    assert _on_hand(client, h, loc, item_id) == Decimal("96")  # deducted once


def test_a_failed_finish_releases_the_claim(client: httpx.Client) -> None:
    h = _admin_headers(client)
    loc, item_id, recipe_id = _setup(client, h)
    # 100 servings of a 4-serving, 8 g recipe needs 200 g; there is 100 g.
    sid = client.post("/api/cook-sessions", json={"recipe_id": recipe_id, "servings_made": 100}, headers=h).json()["id"]
    short = client.post(f"/api/cook-sessions/{sid}/finish", json={"location_id": loc}, headers=h)
    assert short.status_code == 400
    assert client.get(f"/api/cook-sessions/{sid}", headers=h).json()["status"] == "active"
    assert _on_hand(client, h, loc, item_id) == Decimal("100")


# --- ADR 0026 §6.1: one cook-along for the tablet and the agent ---------------


def test_steps_are_ticked_off_and_complete_moves_on(client: httpx.Client) -> None:
    h = _admin_headers(client)
    _, _, recipe_id = _setup(client, h)
    body = client.post("/api/cook-sessions", json={"recipe_id": recipe_id}, headers=h).json()
    sid = body["id"]
    assert [s["done"] for s in body["steps"]] == [False, False, False]
    assert body["steps_left"] == 3 and body["step_done"] is False

    # The tablet ticks step 2 off ahead of time, without moving.
    ticked = client.post(f"/api/cook-sessions/{sid}/steps/2", json={"done": True}, headers=h).json()
    assert ticked["step_number"] == 1 and ticked["completed_steps"] == [2]

    # "Done, what's next?" ticks step 1 and skips the already-done step 2.
    moved = client.post(f"/api/cook-sessions/{sid}/step", json={"action": "complete"}, headers=h).json()
    assert moved["completed_steps"] == [1, 2] and moved["step_number"] == 3
    assert moved["steps_left"] == 1

    # Completing the last step stays on it; everything is done.
    last = client.post(f"/api/cook-sessions/{sid}/step", json={"action": "complete"}, headers=h).json()
    assert last["step_number"] == 3 and last["steps_left"] == 0 and last["step_done"] is True

    # Unticking is allowed, and an out-of-range step is refused.
    unticked = client.post(f"/api/cook-sessions/{sid}/steps/1", json={"done": False}, headers=h).json()
    assert unticked["completed_steps"] == [2, 3]
    assert client.post(f"/api/cook-sessions/{sid}/steps/9", json={"done": True}, headers=h).status_code == 400


def test_the_step_suggests_the_timer_its_words_name(client: httpx.Client) -> None:
    h = _admin_headers(client)
    _, _, recipe_id = _setup(client, h)
    sid = client.post("/api/cook-sessions", json={"recipe_id": recipe_id}, headers=h).json()["id"]
    rest = client.post(f"/api/cook-sessions/{sid}/step", json={"action": "goto", "step": 2}, headers=h).json()
    assert rest["step_timers"] == [{"seconds": 600, "label": "ten minutes"}]


def _ingredient_row(client: httpx.Client, h: dict, recipe_id: str) -> str:
    """The flour line's ingredient ROW id, as the cook screen has it."""
    probe = client.post("/api/cook-sessions", json={"recipe_id": recipe_id}, headers=h).json()
    client.delete(f"/api/cook-sessions/{probe['id']}", headers=h)
    return probe["ingredients"][0]["ingredient_id"]


def test_the_plan_set_on_the_tablet_is_what_any_finish_cooks(client: httpx.Client) -> None:
    """Set up on the tablet ("use 6 g, not 4"), finished by anyone: 6 g leave."""
    h = _admin_headers(client)
    loc, item_id, recipe_id = _setup(client, h)
    ingredient_id = _ingredient_row(client, h, recipe_id)
    started = client.post(
        "/api/cook-sessions",
        json={
            "recipe_id": recipe_id,
            "servings_made": 2,
            "plan": {"ingredient_overrides": [{"ingredient_id": ingredient_id, "quantity": 5}]},
        },
        headers=h,
    )
    assert started.status_code == 201, started.text
    sid = started.json()["id"]
    # Changed mid-cook: progress is kept, the new plan wins.
    client.post(f"/api/cook-sessions/{sid}/step", json={"action": "next"}, headers=h)
    patched = client.patch(
        f"/api/cook-sessions/{sid}",
        json={"plan": {"ingredient_overrides": [{"ingredient_id": ingredient_id, "quantity": 6}]}},
        headers=h,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["step_number"] == 2
    assert patched.json()["plan"]["ingredient_overrides"][0]["quantity"] == "6"

    # The finish carries no ingredient detail — as an agent's would not.
    done = client.post(f"/api/cook-sessions/{sid}/finish", json={"location_id": loc}, headers=h)
    assert done.status_code == 200, done.text
    assert _on_hand(client, h, loc, item_id) == Decimal("94")  # 100 - 6 g


def test_an_omitted_ingredient_in_the_plan_is_not_taken(client: httpx.Client) -> None:
    h = _admin_headers(client)
    loc, item_id, recipe_id = _setup(client, h)
    ingredient_id = _ingredient_row(client, h, recipe_id)
    sid = client.post(
        "/api/cook-sessions",
        json={"recipe_id": recipe_id, "plan": {"ingredient_overrides": [{"ingredient_id": ingredient_id, "omit": True}]}},
        headers=h,
    ).json()["id"]
    assert client.post(f"/api/cook-sessions/{sid}/finish", json={"location_id": loc}, headers=h).status_code == 200
    assert _on_hand(client, h, loc, item_id) == Decimal("100")


def test_timers_run_ring_and_stop(client: httpx.Client, monkeypatch) -> None:
    from datetime import UTC, datetime, timedelta

    from modules.planning.services import cook_timers

    h = _admin_headers(client)
    _, _, recipe_id = _setup(client, h)
    sid = client.post("/api/cook-sessions", json={"recipe_id": recipe_id}, headers=h).json()["id"]

    pasta = client.post(
        "/api/cook-timers", json={"seconds": 600, "label": "Pasta", "session_id": sid, "step_number": 2}, headers=h
    )
    assert pasta.status_code == 201, pasta.text
    assert pasta.json()["state"] == "running" and pasta.json()["remaining_seconds"] == 600
    oven = client.post("/api/cook-timers", json={"seconds": 1200}, headers=h).json()
    assert oven["label"] == "20 min"

    # The session shows them too, soonest first: the tablet and the agent see
    # the same timers.
    session = client.get(f"/api/cook-sessions/{sid}", headers=h).json()
    assert [t["label"] for t in session["timers"]] == ["Pasta", "20 min"]

    # Eleven minutes on: the pasta rings, the oven still runs.
    later = datetime.now(UTC) + timedelta(minutes=11)
    monkeypatch.setattr(cook_timers, "_now", lambda: later)
    states = {t["label"]: t["state"] for t in client.get("/api/cook-timers", headers=h).json()}
    assert states == {"Pasta": "ringing", "20 min": "running"}

    # Stopping a ringing timer dismisses it; stopping a running one cancels it.
    assert client.delete(f"/api/cook-timers/{pasta.json()['id']}", headers=h).json()["stopped"] == "dismissed"
    assert client.delete(f"/api/cook-timers/{oven['id']}", headers=h).json()["stopped"] == "cancelled"
    assert client.get("/api/cook-timers", headers=h).json() == []
    assert client.delete(f"/api/cook-timers/{oven['id']}", headers=h).status_code == 404


def test_timer_bounds(client: httpx.Client) -> None:
    h = _admin_headers(client)
    assert client.post("/api/cook-timers", json={"seconds": 0}, headers=h).status_code == 422
    assert client.post("/api/cook-timers", json={"seconds": 90000}, headers=h).status_code == 422
    missing = client.post(
        "/api/cook-timers", json={"seconds": 60, "session_id": "00000000-0000-0000-0000-000000000000"}, headers=h
    )
    assert missing.status_code == 404


# --- Codex review round 2 -------------------------------------------------------


def test_the_session_shows_the_amounts_its_plan_will_cook(client: httpx.Client) -> None:
    """What the agent reads back is what Finish will take out: an overridden
    amount as typed, an omitted line not at all, "use it all" sized from stock."""
    h = _admin_headers(client)
    loc, item_id, recipe_id = _setup(client, h)
    ingredient_id = _ingredient_row(client, h, recipe_id)

    sid = client.post(
        "/api/cook-sessions",
        json={"recipe_id": recipe_id, "servings_made": 2,
              "plan": {"ingredient_overrides": [{"ingredient_id": ingredient_id, "quantity": 6}]}},
        headers=h,
    ).json()["id"]
    body = client.get(f"/api/cook-sessions/{sid}", headers=h).json()
    assert body["ingredients"][0]["quantity"] == "6"          # not the scaled 4
    assert body["step_ingredients"][0]["quantity"] == "6"     # step 1 mentions flour

    omitted = client.patch(
        f"/api/cook-sessions/{sid}",
        json={"plan": {"ingredient_overrides": [{"ingredient_id": ingredient_id, "omit": True}]}},
        headers=h,
    ).json()
    assert omitted["ingredients"] == [] and omitted["step_ingredients"] == []

    # 100 g on hand, 8 g per recipe: "use it all" is 12.5 recipes of flour.
    use_all = client.patch(
        f"/api/cook-sessions/{sid}", json={"plan": {"scale_to_ingredient_id": ingredient_id}}, headers=h
    ).json()
    assert use_all["ingredients"][0]["quantity"] == "100"


def test_the_database_allows_one_active_cook(client: httpx.Client) -> None:
    from datetime import UTC, datetime
    from uuid import UUID

    import pytest
    from sqlalchemy.exc import IntegrityError

    from app.db.session import SessionLocal
    from modules.planning.models import CookSession

    h = _admin_headers(client)
    _, _, recipe_id = _setup(client, h)
    client.post("/api/cook-sessions", json={"recipe_id": recipe_id}, headers=h)
    now = datetime.now(UTC)
    with SessionLocal() as db:
        db.add(CookSession(recipe_id=UUID(recipe_id), status="active", current_step=0,
                           completed_steps=[], started_at=now, updated_at=now))
        with pytest.raises(IntegrityError):
            db.commit()


def test_a_start_that_loses_the_race_retries_and_wins(client: httpx.Client, monkeypatch) -> None:
    """Another start commits between ours abandoning the old cook and
    inserting the new one: the unique index refuses ours, and the retry
    abandons the newcomer. One active cook, the latest."""
    from uuid import UUID

    from app.db.session import SessionLocal
    from modules.planning.services import cook_sessions

    h = _admin_headers(client)
    _, _, recipe_id = _setup(client, h)
    rival = client.post("/api/cook-sessions", json={"recipe_id": recipe_id}, headers=h).json()["id"]

    real = cook_sessions._abandon_active
    calls = {"n": 0}

    def racing(db, now):
        calls["n"] += 1
        if calls["n"] > 1:  # the first pass "misses" the rival, as a race would
            real(db, now)

    monkeypatch.setattr(cook_sessions, "_abandon_active", racing)
    with SessionLocal() as db:
        session = cook_sessions.start_session(db, recipe_id=UUID(recipe_id))
    assert calls["n"] == 2
    assert client.get(f"/api/cook-sessions/{rival}", headers=h).json()["status"] == "abandoned"
    assert client.get("/api/cook-sessions/active", headers=h).json()["id"] == str(session.id)


def test_two_ticks_at_once_both_stick(client: httpx.Client, monkeypatch) -> None:
    """The tablet ticks step 3 while the agent's "done" on step 1 is in flight:
    the agent's write finds the session changed, reads it again, and keeps both."""
    from uuid import UUID

    from app.db.session import SessionLocal
    from modules.planning.services import cook_sessions

    h = _admin_headers(client)
    _, _, recipe_id = _setup(client, h)
    sid = client.post("/api/cook-sessions", json={"recipe_id": recipe_id}, headers=h).json()["id"]

    real = cook_sessions._require_active
    raced = {"done": False}

    def tablet_ticks_in_between(session):
        real(session)
        if not raced["done"]:
            raced["done"] = True
            with SessionLocal() as other:
                cook_sessions.set_step_done(other, session_id=UUID(sid), step=3, done=True)

    monkeypatch.setattr(cook_sessions, "_require_active", tablet_ticks_in_between)
    with SessionLocal() as db:
        after = cook_sessions.move_step(db, session_id=UUID(sid), action="complete")
    assert sorted(after.completed_steps) == [1, 3]
    assert client.get(f"/api/cook-sessions/{sid}", headers=h).json()["completed_steps"] == [1, 3]


def test_a_substitution_shows_the_substitute_at_its_ratio(client: httpx.Client) -> None:
    h = _admin_headers(client)
    loc, item_id, recipe_id = _setup(client, h)
    rye = client.post("/api/items", json={"name": "Rye", "stock_mode": "measured", "size_unit": "g"}, headers=h).json()["id"]
    sid = client.post(
        "/api/cook-sessions",
        json={"recipe_id": recipe_id, "servings_made": 2,
              "plan": {"substitutions": [{"original_item_id": item_id, "substitute_item_id": rye, "ratio": 1.5}]}},
        headers=h,
    ).json()["id"]
    body = client.get(f"/api/cook-sessions/{sid}", headers=h).json()
    # 8 g flour × 1.5 → 12 g rye for the recipe; half of that for 2 of 4 servings.
    line = body["ingredients"][0]
    assert (line["item"], line["quantity"], line["unit"], line["replaces"]) == ("Rye", "6", "g", "Flour")
    # Step 1 says "flour"; the rye standing in for it is still the step's ingredient.
    assert [i["item"] for i in body["step_ingredients"]] == ["Rye"]


def test_portions_that_do_not_fit_fail_before_any_stock_is_taken(client: httpx.Client) -> None:
    h = _admin_headers(client)
    loc, item_id, _ = _setup(client, h)
    bread = client.post("/api/items", json={"name": "Bread", "stock_mode": "measured", "size_unit": "g"}, headers=h).json()["id"]
    recipe_id = client.post(
        "/api/recipes",
        json={"name": "Loaf", "servings": 4, "output_item_id": bread, "output_quantity": 500, "output_unit": "g",
              "instructions": "Bake.", "ingredients": [{"name": "Flour", "quantity": 8, "unit": "g"}]},
        headers=h,
    ).json()["id"]
    sid = client.post("/api/cook-sessions", json={"recipe_id": recipe_id}, headers=h).json()["id"]

    # Ten 100 g portions from a 500 g loaf: refused, and nothing was deducted.
    over = client.post(
        f"/api/cook-sessions/{sid}/finish",
        json={"location_id": loc, "output_portions": 10, "output_portion_size": 100, "output_portion_unit": "g"},
        headers=h,
    )
    assert over.status_code == 400, over.text
    assert _on_hand(client, h, loc, item_id) == Decimal("100")
    assert client.get(f"/api/cook-sessions/{sid}", headers=h).json()["status"] == "active"


def test_a_finish_that_fails_after_taking_stock_is_not_retryable(client: httpx.Client, monkeypatch) -> None:
    """If a cook fails after its deductions committed, the session is not handed
    back as active — a second Finish would take the stock out again."""
    from modules.planning.services import cook_sessions, recipes

    h = _admin_headers(client)
    loc, item_id, recipe_id = _setup(client, h)
    sid = client.post("/api/cook-sessions", json={"recipe_id": recipe_id, "servings_made": 2}, headers=h).json()["id"]

    real_cook = recipes.cook_recipe

    def cook_then_fail(*args, **kwargs):
        real_cook(*args, **kwargs)
        raise RuntimeError("lost after the deductions")

    monkeypatch.setattr(cook_sessions, "cook_recipe", cook_then_fail)
    failed = client.post(f"/api/cook-sessions/{sid}/finish", json={"location_id": loc}, headers=h)
    assert failed.status_code == 400, failed.text
    assert client.get(f"/api/cook-sessions/{sid}", headers=h).json()["status"] == "finishing"

    monkeypatch.setattr(cook_sessions, "cook_recipe", real_cook)
    again = client.post(f"/api/cook-sessions/{sid}/finish", json={"location_id": loc}, headers=h)
    assert again.status_code == 400
    assert _on_hand(client, h, loc, item_id) == Decimal("96")  # taken once
