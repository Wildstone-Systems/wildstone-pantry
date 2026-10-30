"""Thin HTTP client for the PantryOS REST API, used by the MCP server.

Kept free of any ``mcp`` import so it can be unit-tested against the in-process
app without the MCP runtime. Authenticates with an agent key (``X-Agent-Key``)
plus the optional license key.
"""

from __future__ import annotations

from typing import Any

import httpx

_TIMEOUT = 30


class PantryOSClient:
    def __init__(
        self,
        *,
        agent_key: str,
        base_url: str | None = None,
        license_key: str | None = None,
        http: httpx.Client | None = None,
    ) -> None:
        headers = {"X-Agent-Key": agent_key}
        if license_key:
            headers["X-License-Key"] = license_key
        # ``http`` lets tests inject an in-process client (e.g. Starlette's
        # TestClient); production builds a plain HTTP client from ``base_url``.
        if http is not None:
            http.headers.update(headers)
            self._http = http
        else:
            self._http = httpx.Client(base_url=base_url or "", headers=headers, timeout=_TIMEOUT)
        # The cook this client last asked to finish: a retry after a lost
        # response finds nothing active any more and must ask about THAT one.
        self._finishing_session_id: str | None = None

    def close(self) -> None:
        self._http.close()

    def _get(self, path: str, params: dict | None = None) -> Any:
        resp = self._http.get(path, params={k: v for k, v in (params or {}).items() if v is not None})
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict) -> Any:
        resp = self._http.post(path, json={k: v for k, v in body.items() if v is not None})
        resp.raise_for_status()
        return resp.json()

    def _patch(self, path: str, body: dict) -> Any:
        resp = self._http.patch(path, json={k: v for k, v in body.items() if v is not None})
        resp.raise_for_status()
        return resp.json()

    # --- reads ---
    def list_locations(self) -> Any:
        return self._get("/api/locations")

    def list_inventory(self, location_id: str) -> Any:
        return self._get("/api/inventory", {"location_id": location_id})

    def whats_expiring(self, location_id: str, within_days: int = 7) -> Any:
        return self._get(f"/api/expiring/{location_id}", {"within_days": within_days})

    def list_containers(self, location_id: str | None = None) -> Any:
        return self._get("/api/inventory/containers", {"location_id": location_id})

    def lookup_barcode(self, barcode: str) -> Any:
        return self._get(f"/api/barcodes/lookup/{barcode}")

    def get_summary(self, within_days: int = 7) -> Any:
        return self._get("/api/agent/summary", {"within_days": within_days})

    def list_recipes(self) -> Any:
        return self._get("/api/recipes")

    def get_recipe(self, recipe_id: str) -> Any:
        return self._get(f"/api/recipes/{recipe_id}")

    def suggest_recipes(self, location_id: str | None = None) -> Any:
        if location_id:
            return self._get(f"/api/recipes/suggestions/{location_id}")
        return self._get("/api/recipes/suggestions")

    def get_trends(self, location_id: str, lookback_days: int = 30) -> Any:
        return self._get(f"/api/insights/trends/{location_id}", {"lookback_days": lookback_days})

    def get_restock_suggestions(self, location_id: str) -> Any:
        return self._get(f"/api/insights/restock/{location_id}")

    def list_purchases(self) -> Any:
        return self._get("/api/purchases")

    def list_shopping(self, status: str | None = None) -> Any:
        # This trunk's shopping list returns all entries; `status` is accepted for
        # call-site compatibility and filtered client-side by the caller.
        return self._get("/api/shopping-list")

    def list_household_profiles(self) -> Any:
        return self._get("/api/household/profiles")

    def calories_for(self, profile_id: str, *, on: str | None = None, tz: str | None = None) -> Any:
        return self._get("/api/consumption/calories", {"profile_id": profile_id, "on": on, "tz": tz})

    def household_calories(self, *, on: str | None = None, tz: str | None = None) -> Any:
        return self._get("/api/consumption/calories/summary", {"on": on, "tz": tz})

    def recipe_nutrition(self, recipe_id: str) -> Any:
        return self._get(f"/api/recipes/{recipe_id}/nutrition")

    def search_food(self, query: str, *, limit: int = 10) -> Any:
        return self._get("/api/items/search-nutrition", {"q": query, "limit": limit})

    # --- writes ---
    def log_meal(
        self,
        *,
        item_id: str,
        profile_id: str,
        quantity: float | None = None,
        unit: str = "each",
        location_id: str | None = None,
        weight_grams: float | None = None,
    ) -> Any:
        return self._post(
            "/api/consumption/plate",
            {
                "item_id": item_id,
                "profile_id": profile_id,
                "quantity": quantity,
                "unit": unit,
                "location_id": location_id,
                "weight_grams": weight_grams,
            },
        )

    def delete_meal(self, event_id: str) -> Any:
        resp = self._http.delete(f"/api/consumption/plate/{event_id}")
        resp.raise_for_status()
        return {"deleted": event_id}

    def item_from_food_search(self, *, fdc_id: int, query: str) -> Any:
        return self._post("/api/items/from-food-search", {"fdc_id": fdc_id, "q": query})

    def cook_recipe(
        self,
        recipe_id: str,
        *,
        location_id: str | None = None,
        servings: int = 1,
        leftover_servings: int = 0,
        note: str | None = None,
        servings_made: float | None = None,
        output_weight_grams: float | None = None,
        output_portions: int | None = None,
        output_portion_size: float | None = None,
        output_portion_unit: str | None = None,
        scale_to_ingredient_id: str | None = None,
        allow_short_ingredients: bool = False,
    ) -> Any:
        payload = {
            "location_id": location_id,
            "servings": servings,
            "leftover_servings": leftover_servings,
            "note": note,
        }
        # Omitted rather than sent as null: the cook endpoint distinguishes "not
        # measured" from a value, and a null portion count is not the same
        # request as no portioning at all.
        optional = {
            "servings_made": servings_made,
            "output_weight_grams": output_weight_grams,
            "output_portions": output_portions,
            "output_portion_size": output_portion_size,
            "output_portion_unit": output_portion_unit,
            "scale_to_ingredient_id": scale_to_ingredient_id,
            "allow_short_ingredients": allow_short_ingredients or None,
        }
        payload.update({key: value for key, value in optional.items() if value is not None})
        return self._post(f"/api/recipes/{recipe_id}/cook", payload)

    # --- cook sessions (ADR 0026 §6): step-by-step, stock touched only at finish ---
    def start_cooking(self, recipe_id: str, *, servings: float | None = None) -> Any:
        self._finishing_session_id = None  # a new cook: an old finish is not its answer
        return self._post("/api/cook-sessions", {"recipe_id": recipe_id, "servings_made": servings})

    def active_cook_session(self) -> Any:
        resp = self._http.get("/api/cook-sessions/active")
        resp.raise_for_status()
        # 204 = nothing is being cooked.
        return None if resp.status_code == 204 else resp.json()

    def _require_cooking(self) -> dict:
        session = self.active_cook_session()
        if session is None:
            raise RuntimeError("Nothing is being cooked. Start a recipe with start_cooking first.")
        return session

    def move_cook_step(self, action: str, *, step: int | None = None) -> Any:
        session = self._require_cooking()
        return self._post(f"/api/cook-sessions/{session['id']}/step", {"action": action, "step": step})

    def complete_cook_step(self, *, step: int | None = None) -> Any:
        return self.move_cook_step("complete", step=step)

    def finish_cooking(self, *, location_id: str | None = None) -> Any:
        """Finish the cook in progress. Repeat-safe: if the last finish's answer
        was lost in transit, nothing is active any more, and the retry goes to
        that same session — which answers with the cook already recorded (the
        server never cooks a session twice)."""
        session = self.active_cook_session()
        session_id = session["id"] if session is not None else self._finishing_session_id
        if session_id is None:
            raise RuntimeError("Nothing is being cooked. Start a recipe with start_cooking first.")
        # Remembered only while this finish's outcome is unknown: kept if the
        # request is lost in transit (so a retry asks about the same cook),
        # forgotten once PantryOS has answered either way.
        self._finishing_session_id = session_id
        try:
            result = self._post(f"/api/cook-sessions/{session_id}/finish", {"location_id": location_id})
        except httpx.TransportError:
            raise
        except Exception:
            self._finishing_session_id = None
            raise
        self._finishing_session_id = None
        return result

    # --- kitchen timers (ADR 0026 §6.1) ---
    def list_timers(self) -> Any:
        resp = self._http.get("/api/cook-timers")
        resp.raise_for_status()
        return resp.json()

    def set_timer(self, seconds: int, *, label: str | None = None) -> Any:
        # Tied to the cook in progress and its step, when there is one.
        session = self.active_cook_session()
        payload: dict[str, Any] = {"seconds": seconds, "label": label}
        if session is not None:
            payload["session_id"] = session["id"]
            payload["step_number"] = session.get("step_number") or None
        return self._post("/api/cook-timers", payload)

    def stop_timer(self, *, timer_id: str | None = None, label: str | None = None) -> Any:
        if timer_id is None:
            live = self.list_timers()
            want = (label or "").strip().lower()
            matches = [t for t in live if not want or want in t["label"].lower()]
            exact = [t for t in matches if t["label"].lower() == want]
            matches = exact or matches
            if not matches:
                raise RuntimeError(f"No timer called {label!r} is running." if label else "No timer is running.")
            if len(matches) > 1:
                names = ", ".join(t["label"] for t in matches)
                raise RuntimeError(f"More than one timer matches ({names}); say which.")
            timer_id = matches[0]["id"]
        resp = self._http.delete(f"/api/cook-timers/{timer_id}")
        resp.raise_for_status()
        return resp.json()

    def stop_cooking(self) -> Any:
        self._finishing_session_id = None
        session = self._require_cooking()
        resp = self._http.delete(f"/api/cook-sessions/{session['id']}")
        resp.raise_for_status()
        return resp.json()

    def add_to_shopping_list(
        self,
        *,
        name: str | None = None,
        item_id: str | None = None,
        desired_quantity: float | None = None,
        unit: str = "each",
        note: str | None = None,
    ) -> Any:
        return self._post(
            "/api/shopping-list",
            {"name": name, "item_id": item_id, "quantity": desired_quantity, "unit": unit, "note": note},
        )

    def set_shopping_status(self, shopping_item_id: str, status: str) -> Any:
        return self._patch(f"/api/shopping-list/{shopping_item_id}", {"status": status})

    def add_inventory(
        self,
        *,
        item_id: str,
        location_id: str,
        quantity: float,
        unit: str = "each",
        expiration_date: str | None = None,
        container_id: str | None = None,
    ) -> Any:
        return self._post(
            "/api/inventory/add",
            {
                "item_id": item_id,
                "location_id": location_id,
                "quantity": quantity,
                "unit": unit,
                "expiration_date": expiration_date,
                "container_id": container_id,
            },
        )

    def ingest_product_url(
        self,
        *,
        url: str,
        add_to_shopping: bool = True,
        add_to_inventory: bool = False,
        location_id: str | None = None,
        container_id: str | None = None,
        quantity: float = 1,
        unit: str = "each",
    ) -> Any:
        return self._post(
            "/api/agent/ingest-product-url",
            {
                "url": url,
                "add_to_shopping": add_to_shopping,
                "add_to_inventory": add_to_inventory,
                "location_id": location_id,
                "container_id": container_id,
                "quantity": quantity,
                "unit": unit,
            },
        )
