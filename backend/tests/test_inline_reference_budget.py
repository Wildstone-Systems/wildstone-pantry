"""Inline enrichment shares one budget across nutrition, density and retries."""
import httpx

from modules.catalog.services import barcode_providers, enrichment, reference_budget


def test_batch_exhaustion_skips_density_and_remaining_items(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(reference_budget.time, "monotonic", lambda: now[0])
    calls = []

    class Bare:
        name = "Bare"
        energy_kcal_100g = None
        nutrition_estimated = False
        density_g_per_ml = None

    def nutrition(*args, **kwargs):
        calls.append("nutrition")
        now[0] += enrichment.INLINE_REFERENCE_BUDGET_SECONDS

    monkeypatch.setattr("modules.catalog.services.barcodes._apply_estimated_nutrition", nutrition)
    monkeypatch.setattr("modules.catalog.services.conversions.suggest_density", lambda *a, **k: calls.append("density"))
    result = enrichment.enrich_new_items(None, [Bare(), Bare(), Bare()])
    assert calls == ["nutrition"]
    assert result == {"enriched": {}, "skipped": 2}
    assert reference_budget.remaining() is None  # context never leaks into later requests


def test_provider_retry_will_not_sleep_past_inline_budget(monkeypatch):
    calls = []
    sleeps = []
    monkeypatch.setattr(barcode_providers.time, "sleep", lambda value: sleeps.append(value))

    def request(url, **kwargs):
        calls.append(kwargs["timeout"])
        return httpx.Response(429, headers={"Retry-After": "4"}, request=httpx.Request("GET", url))

    monkeypatch.setattr(barcode_providers.httpx, "get", request)
    import pytest
    with reference_budget.budget(1):
        with pytest.raises(TimeoutError):
            barcode_providers._provider_get("https://fixture.invalid", params={}, timeout=6, label="fixture")
    assert len(calls) == 1
    assert 0 < calls[0] <= 0.25
    assert not sleeps


def test_judge_gets_remaining_transport_budget_and_fails_closed(monkeypatch):
    from modules.automation import food_judge
    monkeypatch.setattr(food_judge, "_ollama_config", lambda db: {"base_url": "https://fixture.invalid", "model": "fixture"})
    timeouts = []

    def request(*args, **kwargs):
        timeouts.append(kwargs["timeout"])
        raise httpx.ReadTimeout("fixture")

    monkeypatch.setattr(food_judge.httpx, "post", request)
    with reference_budget.budget(2):
        verdict = food_judge.same_food(None, product="Food", reference="Food")
    assert verdict.checked is False
    assert 0 < timeouts[0] <= 0.5
