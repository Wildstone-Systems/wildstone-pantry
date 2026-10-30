"""Immutable checked-in SYNTHETIC proposals; these are not human labels."""

import hashlib
import json
from pathlib import Path

from modules.automation.benchmark_contract import CASE_LIMIT

LABELS = {
    "culinary_identity": ["same", "different", "substitution", "insufficient"],
    "nutrition_transfer": ["eligible", "unsuitable", "insufficient"],
    "density_transfer": ["eligible", "unsuitable", "insufficient"],
    "receipt_role": ["stock", "adjustment", "return", "insufficient"],
    "recipe_plausibility": ["ingredient", "instruction", "heading", "compound", "review_required", "insufficient"],
    "contextual_substitution": ["review_required", "insufficient"],
}
PROMPT_VERSION = "synthetic-task-prompts.v1"
RUBRICS = {
    "culinary_identity": {
        "description": "Same edible ingredient in the same meaningful form; identity is not substitution.",
        "labels": {
            "same": "Same ingredient; only brand, regional synonym or packaging differs",
            "different": "Meaningful flavor, composition, preparation or blend differs",
            "substitution": "Different form/ingredient could be a substitution needing approval",
            "insufficient": "Ambiguous wording or missing evidence prevents a judgment",
        },
    },
    "nutrition_transfer": {
        "description": "Whether reference nutrition can transfer unchanged, not merely culinary compatibility.",
        "labels": {
            "eligible": "Evidence establishes matching edible composition, fat/sugar/salt and preparation basis",
            "unsuitable": "Known composition, concentration, preparation or edible basis differs",
            "insufficient": "Composition/preparation evidence needed for transfer is absent",
        },
    },
    "density_transfer": {
        "description": "Whether the same measured mass per volume can transfer; a model supplies no numeric density.",
        "labels": {
            "eligible": "Matching edible material, particle form, preparation and packing basis are established",
            "unsuitable": "Known grind, packing, hydration, preparation or composition differs",
            "insufficient": "Necessary physical form or packing evidence is missing",
        },
    },
    "receipt_role": {
        "description": "Draft line role only, never an inventory write or arithmetic decision.",
        "labels": {
            "stock": "A purchased edible stock candidate",
            "adjustment": "Payment, discount, fee, non-food, summary or void",
            "return": "Returned stock needing separate review, not a positive purchase",
            "insufficient": "Truncation or missing context prevents a reliable role",
        },
    },
    "recipe_plausibility": {
        "description": "Line role or anomaly requiring review; no recipe correctness or allergy safety claim.",
        "labels": {
            "ingredient": "One plausible ingredient with adequate supplied quantity/preparation context",
            "instruction": "A cooking action, not an ingredient",
            "heading": "A section/title, not an ingredient",
            "compound": "Multiple ingredients requiring separation",
            "review_required": "Explicit ambiguity, alternatives, quantity roles or restriction conflict needs review",
            "insufficient": "Required ingredient, quantity, preparation or restriction evidence absent",
        },
    },
    "contextual_substitution": {
        "description": "Proposed swap direction and approval scope only; conditional tracker #100 remains closed.",
        "labels": {
            "review_required": "Specific swap/context/scope is supplied but requires explicit human approval",
            "insufficient": "Direction, role, recipe context, ratio or restriction evidence is missing",
        },
    },
}


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def load():
    corpus = json.loads((Path(__file__).parent / "fixtures" / "classifier-corpus-v2.json").read_text(encoding="utf-8"))
    corpus["rubrics"] = RUBRICS
    if not 200 <= len(corpus["cases"]) <= CASE_LIMIT:
        raise ValueError("Corpus outside bounded review size")
    groups, evidence, identifiers = {}, {}, set()
    for case in corpus["cases"]:
        if case["id"] in identifiers or case["proposed_label"] not in LABELS[case["task"]]:
            raise ValueError("Invalid fixture identity/label")
        identifiers.add(case["id"])
        group = case["leakage_group"]
        if group in groups and groups[group] != case["split"]:
            raise ValueError("Leakage group crosses split")
        groups[group] = case["split"]
        text = digest({key: " ".join(value.lower().split()) for key, value in case["evidence"].items()})
        if text in evidence and evidence[text] != case["split"]:
            raise ValueError("Duplicate evidence crosses split")
        evidence[text] = case["split"]
    return corpus


def request_for(case):
    # Target label, critical marker, split and reviewer data NEVER enter the prompt.
    labels = LABELS[case["task"]]
    instruction = (
        f"Classify the supplied synthetic evidence for task {case['task']}. "
        + RUBRICS[case["task"]]["description"]
        + " "
        "Culinary identity, nutrition transfer and density transfer are separate purposes. "
        "Missing evidence requires insufficient. A substitute is never the same food. "
        "No label authorizes safety, an allergy claim, numeric conversion, stock writes or substitution approval. "
        "For contextual_substitution use review_required when a specific direction/context needs human approval."
    )
    return {
        "state": json.dumps(case["evidence"], ensure_ascii=False),
        "questions": {
            "q_1": {
                "type": "choice",
                "instructions": instruction,
                "criteria": {
                    f"c_{i+1}": f"{label}: {RUBRICS[case['task']]['labels'][label]}" for i, label in enumerate(labels)
                },
            }
        },
    }
