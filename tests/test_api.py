"""
Tests for src/api.py's FastAPI app: /health, input validation on /extract,
and the new API-key auth + per-client rate limiting.

process_note (the actual Gemini + MCP pipeline) is mocked out everywhere
here — these tests are about the HTTP layer, not the AI pipeline itself.
"""
from unittest.mock import patch

import pytest

from src import api as api_module


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["model"] == api_module.MODEL_NAME


def test_extract_rejects_empty_text(client):
    resp = client.post("/extract", json={"text": ""})
    assert resp.status_code == 400


def test_extract_rejects_whitespace_only_text(client):
    resp = client.post("/extract", json={"text": "   "})
    assert resp.status_code == 400


def test_extract_missing_field_returns_422(client):
    resp = client.post("/extract", json={})
    assert resp.status_code == 422


def test_extract_happy_path_calls_pipeline(client):
    fake_result = {
        "note_text": "patient has a headache",
        "extraction": {"summary": "Headache reported.", "urgency": "low"},
        "extraction_valid": True,
        "diagnosis_reasoning": {"diagnosis": None},
        "reasoning_valid": True,
    }
    with patch.object(api_module, "process_note", return_value=fake_result) as mock_process:
        resp = client.post("/extract", json={"text": "patient has a headache"})
    assert resp.status_code == 200
    assert resp.json() == fake_result
    mock_process.assert_called_once_with("patient has a headache")


def test_extract_pipeline_error_returns_502(client):
    with patch.object(api_module, "process_note", side_effect=RuntimeError("boom")):
        resp = client.post("/extract", json={"text": "some note"})
    assert resp.status_code == 502


# ---------------------------------------------------------------------------
# API key auth
# ---------------------------------------------------------------------------
def test_extract_allowed_without_key_when_auth_disabled(client, monkeypatch):
    monkeypatch.setattr(api_module, "_REQUIRED_API_KEY", None)
    with patch.object(api_module, "process_note", return_value={"ok": True}):
        resp = client.post("/extract", json={"text": "hello"})
    assert resp.status_code == 200


def test_extract_rejects_missing_key_when_auth_enabled(client, monkeypatch):
    monkeypatch.setattr(api_module, "_REQUIRED_API_KEY", "secret123")
    resp = client.post("/extract", json={"text": "hello"})
    assert resp.status_code == 401


def test_extract_rejects_wrong_key_when_auth_enabled(client, monkeypatch):
    monkeypatch.setattr(api_module, "_REQUIRED_API_KEY", "secret123")
    resp = client.post("/extract", json={"text": "hello"}, headers={"X-API-Key": "wrong"})
    assert resp.status_code == 401


def test_extract_accepts_correct_key_when_auth_enabled(client, monkeypatch):
    monkeypatch.setattr(api_module, "_REQUIRED_API_KEY", "secret123")
    with patch.object(api_module, "process_note", return_value={"ok": True}):
        resp = client.post("/extract", json={"text": "hello"}, headers={"X-API-Key": "secret123"})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
def test_rate_limit_blocks_after_threshold(client, monkeypatch):
    monkeypatch.setattr(api_module, "_RATE_LIMIT_PER_MINUTE", 3)
    api_module._request_log.clear()
    with patch.object(api_module, "process_note", return_value={"ok": True}):
        for _ in range(3):
            resp = client.post("/extract", json={"text": "hello"})
            assert resp.status_code == 200
        blocked = client.post("/extract", json={"text": "hello"})
    assert blocked.status_code == 429


def test_rate_limit_is_isolated_per_api_key(client, monkeypatch):
    monkeypatch.setattr(api_module, "_REQUIRED_API_KEY", "secret123")
    monkeypatch.setattr(api_module, "_RATE_LIMIT_PER_MINUTE", 1)
    api_module._request_log.clear()
    with patch.object(api_module, "process_note", return_value={"ok": True}):
        r1 = client.post("/extract", json={"text": "hi"}, headers={"X-API-Key": "secret123"})
        r2 = client.post("/extract", json={"text": "hi"}, headers={"X-API-Key": "secret123"})
    assert r1.status_code == 200
    assert r2.status_code == 429  # second call from the same key hits the limit
