"""Request-validation errors must stay 422 even when the input is a non-finite float.

Python's JSON parser accepts the bare literals NaN / Infinity / -Infinity (not
valid JSON, but common from Python clients). FastAPI's default 422 body echoes
the offending input back, which the encoder cannot serialise — it used to
surface as a 500 / dropped connection on any numeric field.
"""

from __future__ import annotations

import httpx
import pytest

_HEADERS = {"Content-Type": "application/json"}


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_bare_non_finite_literal_is_a_422_not_a_500(client: httpx.Client, literal: str) -> None:
    resp = client.post("/api/recipes", content=f'{{"name": "x", "servings": {literal}}}', headers=_HEADERS)
    assert resp.status_code == 422, resp.text
    errors = resp.json()["detail"]
    assert errors and errors[0]["loc"][-1] == "servings"
    assert errors[0]["input"] == str(float(literal))


def test_ordinary_validation_error_shape_is_unchanged(client: httpx.Client) -> None:
    resp = client.post("/api/recipes", json={"name": "x", "servings": "lots"})
    assert resp.status_code == 422
    first = resp.json()["detail"][0]
    assert set(first) >= {"type", "loc", "msg", "input"}
    assert first["input"] == "lots"
