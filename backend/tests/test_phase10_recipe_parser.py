from __future__ import annotations

import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from modules.automation.recipe_import_parser import parse_recipe_text


def test_phase10_parse_recipe_text_sections() -> None:
    raw = """
Pancakes

Ingredients:
1 cup flour
2 eggs
1/2 cup milk

Instructions:
Mix.
Cook.
"""
    parsed = parse_recipe_text(raw)
    assert parsed["title"] == "Pancakes"
    assert "Mix." in parsed["instructions"]
    assert len(parsed["ingredients"]) >= 3
    names = [row["name"].lower() for row in parsed["ingredients"]]
    assert any("flour" in n for n in names)
    assert any("eggs" in n for n in names)
