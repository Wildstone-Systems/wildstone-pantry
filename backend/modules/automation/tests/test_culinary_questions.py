"""Evidence boundaries and provider-answer behavior for culinary review questions."""

import json

import pytest

from modules.automation.culinary_questions import context_request, decode_answers, identity_request


def answer(choice, probabilities, provider_confidence=.99):
    return {"type": "choice", "choice": choice, "probabilities": probabilities, "confidence": provider_confidence}


def test_identification_excludes_recipe_influence_but_preserves_user_evidence_sources():
    packet = {
        "product": {"name": "Smoked chipotle seasoning", "ingredients_text": "chipotle pepper, salt, garlic", "nutrition_basis": "per 100 g", "nutrition": {"salt_g": 20}},
        "requested": {"name": "chipotle powder"},
        "facts": {"identity": "chipotle blend", "form": "powder", "preparation": None, "composition": "salt and garlic", "recipe_role": "smoky chili"},
        "resolved": {"identity": "chipotle blend", "form": "powder"},
        "fact_sources": {"identity": "user supplied label", "form": "user observation", "preparation": "unknown", "composition": "packaging", "recipe_role": "chili instruction"},
        "recipe_name": "Secret chili", "recipe_instructions": "Recipe permits paprika", "expected_answer": "c_1", "reason": "hidden answer key",
    }
    request = identity_request(packet)
    state = json.loads(request.state)["evidence"]
    assert state["facts"] == {key: packet["facts"][key] for key in ("identity", "form", "preparation", "composition")}
    assert state["resolved"] == packet["resolved"]
    assert state["fact_sources"]["identity"] == "user supplied label"
    assert state["facts"]["preparation"] is None
    assert state["product"]["nutrition"]["salt_g"] == 20
    assert "recipe_role" not in request.state
    assert "Secret chili" not in request.state and "paprika" not in request.state
    assert "expected_answer" not in request.state and "hidden answer key" not in request.state
    assert set(request.questions) == {"q_1"}


def test_context_uses_actual_recipe_and_resolved_facts_without_fabricating_unknowns():
    packet = {"product": {"name": "Whole cumin"}, "requested": {"name": "ground cumin"}, "recipe_name": "Chili", "recipe_instructions": "Grinder available; whole seeds unwanted", "recipe_ingredient_line": "1 tsp ground cumin", "facts": {"identity": "cumin", "recipe_role": "spice"}, "resolved": {"form": "whole seeds"}, "fact_sources": {"recipe_role": "recipe instruction"}, "quantity": 1, "unit": "tsp"}
    state = json.loads(context_request(packet).state)["evidence"]
    assert state["recipe_instructions"] == packet["recipe_instructions"]
    assert state["recipe_ingredient_line"] == packet["recipe_ingredient_line"]
    assert state["resolved"] == {"form": "whole seeds"}
    assert state["fact_sources"]["recipe_role"] == "recipe instruction"
    assert "preparation" not in state["facts"]
    assert "quantity" not in state and "unit" not in state
    request = context_request(packet)
    assert set(request.questions) == {"q_1", "q_2"}
    assert "numeric ratio" in request.questions["q_1"].instructions
    assert "user confirmation" in request.questions["q_1"].instructions
    assert "unknown/inapplicable" in request.questions["q_2"].instructions



def test_actual_supplied_nutrition_preserves_basis_estimated_flags_and_nulls_without_private_ids():
    packet = {
        "product": {"name": "Chipotle blend", "nutrition_values": {"salt_g": "20.0", "energy_kcal": None, "serving_size_g": "5"}, "nutrition_estimated": False, "nutrition_basis": "per100g", "nutrition_source": "product label", "source_fingerprint": "opaque-product-hash", "provider": "private-provider", "id": 123},
        "requested": {"name": "Chipotle powder", "nutrition_values": {"salt_g": None}, "nutrition_estimated": True, "nutrition_source": "existing estimate", "nutrition_basis": None, "source_fingerprint": "opaque-reference-hash"},
        "settings": {"key": "private-setting"},
    }
    for request in (identity_request(packet), context_request(packet)):
        evidence = json.loads(request.state)["evidence"]
        assert evidence["product"]["nutrition_values"] == packet["product"]["nutrition_values"]
        assert evidence["product"]["nutrition_estimated"] is False
        assert evidence["product"]["nutrition_source"] == "product label"
        assert evidence["requested"]["nutrition_values"]["salt_g"] is None
        assert evidence["requested"]["nutrition_estimated"] is True
        assert evidence["requested"]["nutrition_basis"] is None
        assert "opaque-" not in request.state and "private-" not in request.state
        assert "source_fingerprint" not in request.state and '"id"' not in request.state


def test_large_unicode_evidence_is_valid_bounded_deterministic_and_honestly_truncated():
    packet = {"product": {"name": "🌶" * 10000, "ingredients_text": "garlic " * 10000, "nutrition": {str(i): "界" * 10000 for i in range(80)}}, "facts": {"composition": "salt " * 10000}}
    request = context_request(packet)
    assert len(request.state) <= 8000
    assert len(json.dumps(request.model_dump(), ensure_ascii=False).encode()) <= 32 * 1024
    assert json.loads(request.state)["evidence_truncated"] is True
    assert request.state == context_request(packet).state


@pytest.mark.parametrize("choice,label", [("c_1", "same"), ("c_2", "same_base"), ("c_3", "different"), ("c_4", "insufficient")])
def test_named_identity_relations_decode_without_provider_confidence_authority(choice, label):
    probabilities = {f"c_{i}": .1 for i in range(1, 5)}
    probabilities[choice] = .7
    row = decode_answers("identification", {"q_1": answer(choice, probabilities)}, .90)[0]
    assert row["label"] == label
    assert row["choice"] == choice and row["probability"] == pytest.approx(.7)
    assert row["provider_confidence"] == .99
    assert row["clears_threshold"] is False
    assert set(row) == {"purpose", "choice", "label", "probability", "provider_confidence", "margin", "clears_threshold"}


def test_context_keeps_match_and_adjustment_separate_and_checks_saved_threshold_and_margin():
    rows = decode_answers("context", {
        "q_1": answer("c_2", {"c_1": .02, "c_2": .94, "c_3": .02, "c_4": .02}, .01),
        "q_2": answer("c_3", {"c_1": .03, "c_2": .03, "c_3": .88, "c_4": .03, "c_5": .03}),
    }, .90)
    assert [(r["purpose"], r["label"]) for r in rows] == [("recipe_matching", "possible"), ("adjustment", "seasoning")]
    assert rows[0]["clears_threshold"] is True and rows[0]["provider_confidence"] == .01
    assert rows[1]["clears_threshold"] is False
    low_margin = decode_answers("identification", {"q_1": answer("c_1", {"c_1": .55, "c_2": .4, "c_3": .03, "c_4": .02})}, .5)[0]
    assert low_margin["clears_threshold"] is False


@pytest.mark.parametrize("match_choice,match_label,adjustment_choice,adjustment_label", [
    ("c_1", "direct", "c_1", "none"),
    ("c_2", "possible", "c_2", "preparation"),
    ("c_2", "possible", "c_3", "seasoning"),
    ("c_2", "possible", "c_4", "texture"),
    ("c_3", "not_suitable", "c_5", "unknown"),
    ("c_4", "insufficient", "c_5", "unknown"),
])
def test_closed_context_vocabulary_covers_direct_substitute_and_manual_fallback(match_choice, match_label, adjustment_choice, adjustment_label):
    match = {f"c_{i}": .02 for i in range(1, 5)}
    match[match_choice] = .94
    adjustment = {f"c_{i}": .01 for i in range(1, 6)}
    adjustment[adjustment_choice] = .96
    decoded = decode_answers("context", {"q_1": answer(match_choice, match), "q_2": answer(adjustment_choice, adjustment)}, .90)
    assert [row["label"] for row in decoded] == [match_label, adjustment_label]
    assert all(row["clears_threshold"] for row in decoded)


@pytest.mark.parametrize("answers", [
    {},
    {"q_1": answer("c_9", {"c_1": .7, "c_2": .1, "c_3": .1, "c_4": .1})},
    {"q_1": answer("c_2", {"c_1": .7, "c_2": .1, "c_3": .1, "c_4": .1})},
    {"q_1": answer("c_1", {"c_1": .9, "c_2": .1, "c_3": .1, "c_4": .1})},
])
def test_malformed_or_inconsistent_provider_answers_fail_closed(answers):
    with pytest.raises(ValueError):
        decode_answers("identification", answers, .9)


@pytest.mark.parametrize("confidence", [True, float("nan"), float("inf"), -.1, 1.1])
def test_invalid_threshold_cannot_authorize_a_prediction(confidence):
    with pytest.raises(ValueError):
        decode_answers("identification", {}, confidence)
