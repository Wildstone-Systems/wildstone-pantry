"""Real planning packet to classifier prompt contract regression."""

import json

from modules.automation.culinary_questions import context_request, identity_request
from modules.planning.schemas.ingredient_match_review import MatchPacket


def test_real_match_packet_recipe_constraints_reach_context_but_cannot_bias_identity():
    def item(name):
        return {"item_id": "00000000-0000-0000-0000-000000000001", "name": name, "brand": None, "ingredients_text": None, "source": "manual", "canonical_ingredient": None, "canonical_provenance": "unknown", "canonical_version": None, "canonical_revision": 0, "nutrition_basis": "unknown", "nutrition_source": None, "nutrition_estimated": False, "nutrition_values": {"salt_g": None}, "source_fingerprint": "opaque-source-hash"}

    packet = MatchPacket.model_validate({
        "product": item("Vanilla Greek yogurt"), "requested": item("Plain unsweetened Greek yogurt"),
        "recipe_id": "00000000-0000-0000-0000-000000000002", "ingredient_id": "00000000-0000-0000-0000-000000000003",
        "recipe_name": "Savory garlic sauce", "recipe_instructions": "Savory sauce: no vanilla or added sweetness is acceptable. Sugar and vanilla cannot be removed.",
        "recipe_source_fingerprint": "opaque-recipe-hash", "recipe_ingredient_line": "Plain unsweetened Greek yogurt", "quantity": "200", "unit": "g",
        "facts": {"composition": "cultured milk, sugar, vanilla"},
        "resolved": {"identity": "Greek yogurt", "form": "cultured yogurt", "preparation": "refrigerated", "composition": "cultured milk, sugar, vanilla", "recipe_role": "savory sauce"},
        "fact_sources": {"identity": "user_provided", "form": "user_provided", "preparation": "user_provided", "composition": "user_provided", "recipe_role": "user_provided"},
    }).model_dump(mode="json")
    context = json.loads(context_request(packet).state)["evidence"]
    assert context["recipe_instructions"] == packet["recipe_instructions"]
    assert context["recipe_ingredient_line"] == "Plain unsweetened Greek yogurt"
    assert context["resolved"]["recipe_role"] == "savory sauce"
    identity = json.loads(identity_request(packet).state)["evidence"]
    assert "recipe_instructions" not in identity and "recipe_name" not in identity
    assert "recipe_role" not in identity["resolved"]
    assert "cannot be removed" not in identity_request(packet).state
    assert "opaque-recipe-hash" not in context_request(packet).state
