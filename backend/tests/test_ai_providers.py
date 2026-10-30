"""AI provider seam (ADR 0018 §9): registry resolution, local providers,
and the rules→AI receipt-preview fallback — AI output is always a draft."""

from __future__ import annotations

import json

import httpx
import pytest

from app.db.session import SessionLocal
from modules.automation.providers import CAP_VISION, AIUnavailable, resolve_provider
from modules.automation.providers.local import OllamaProvider
from modules.shopping.services.connector_config import clear_config, set_config

_OLLAMA_REPLY = {
    "message": {
        "content": json.dumps(
            {
                "lines": [
                    {"name": "Whole Milk", "quantity": "1", "unit": "each", "raw_text": "WHL MLK 3.49", "confidence": 0.93},
                    {"name": "Bananas", "quantity": "2.1", "unit": "lb", "raw_text": "BANANAS 1.26", "confidence": 0.8},
                    {"name": "", "quantity": "?", "unit": "?"},  # garbage — dropped
                ]
            }
        )
    }
}


@pytest.fixture()
def db():
    with SessionLocal() as session:
        yield session


def _configure_ollama(db) -> None:
    set_config(db, "ollama", {"base_url": "http://localhost:11434", "model": "llava"})


def test_registry_resolves_none_when_nothing_configured(db) -> None:
    assert resolve_provider(db, CAP_VISION) is None


def test_registry_local_wins(db) -> None:
    _configure_ollama(db)
    provider = resolve_provider(db, CAP_VISION)
    assert provider is not None and provider.key == "ollama"
    clear_config(db, "ollama")
    assert resolve_provider(db, CAP_VISION) is None


def test_ollama_extract_receipt_parses_and_drops_garbage(db, monkeypatch) -> None:
    _configure_ollama(db)

    def fake_post(url: str, *args, **kwargs):
        assert url.endswith("/api/chat")
        assert kwargs["json"]["model"] == "llava"
        return httpx.Response(200, json=_OLLAMA_REPLY, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    extraction = OllamaProvider().extract_receipt(db, b"fake-image-bytes", media_type="image/jpeg")
    assert extraction.provider == "ollama"
    assert [line.name for line in extraction.lines] == ["Whole Milk", "Bananas"]
    assert all(line.source == "ai" for line in extraction.lines)
    assert extraction.lines[0].confidence == 0.93


def test_ollama_unconfigured_raises_unavailable(db) -> None:
    with pytest.raises(AIUnavailable):
        OllamaProvider().extract_receipt(db, b"x", media_type="image/jpeg")


def _auth_headers(client: httpx.Client) -> dict[str, str]:
    email, password = "ai-admin@example.com", "AiPass9999!"
    client.post("/api/auth/bootstrap-admin", json={"email": email, "password": password})
    login = client.post("/api/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {login.json()['token']}"}


def test_preview_image_falls_back_to_ai_when_ocr_missing(client: httpx.Client, db, monkeypatch) -> None:
    headers = _auth_headers(client)
    _configure_ollama(db)

    # Simulate a box without tesseract: the deterministic path 503s...
    import modules.shopping.services.receipts as receipts_service

    def no_ocr(_image: bytes) -> str:
        raise RuntimeError("OCR dependencies not installed")

    monkeypatch.setattr(receipts_service, "image_bytes_to_text", no_ocr, raising=False)
    import modules.automation

    monkeypatch.setattr(modules.automation, "image_bytes_to_text", no_ocr)

    def fake_post(url: str, *args, **kwargs):
        return httpx.Response(200, json=_OLLAMA_REPLY, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)

    location = client.post("/api/locations", json={"name": "AI Pantry"}, headers=headers).json()
    resp = client.post(
        f"/api/receipts/preview-image?location_id={location['id']}",
        files={"file": ("receipt.jpg", b"fake-image-bytes", "image/jpeg")},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["engine"] == "ollama"
    assert body["line_count"] == 2
    assert all(line["source"] == "ai" for line in body["lines"])
    assert body["lines"][0]["expiration_days_estimate"] is not None  # drafts still get estimates


def test_preview_image_503s_with_no_provider_and_no_ocr(client: httpx.Client, monkeypatch) -> None:
    headers = _auth_headers(client)
    import modules.automation

    def no_ocr(_image: bytes) -> str:
        raise RuntimeError("OCR dependencies not installed")

    monkeypatch.setattr(modules.automation, "image_bytes_to_text", no_ocr)

    location = client.post("/api/locations", json={"name": "No AI Pantry"}, headers=headers).json()
    resp = client.post(
        f"/api/receipts/preview-image?location_id={location['id']}",
        files={"file": ("receipt.jpg", b"fake-image-bytes", "image/jpeg")},
        headers=headers,
    )
    assert resp.status_code == 503
    assert "No AI provider" in resp.json()["detail"]
