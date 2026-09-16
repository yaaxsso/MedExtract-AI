"""
Shared pytest fixtures.

src/api.py does real work at import time (reads env vars, raises RuntimeError
if no Gemini key is set, constructs genai.Client instances). Tests never want
to hit the real Gemini API or real external medical APIs, so this file:

  1. Sets dummy env vars *before* any test module imports src.api, so the
     module-level setup succeeds without a real key.
  2. Provides a `client` fixture (FastAPI TestClient) for the API tests.

genai.Client(api_key="dummy-test-key") itself does not make a network call —
it only makes one when a method like .models.generate_content(...) is
actually invoked, which the API tests mock out.
"""
import os

os.environ.setdefault("GEMINI_API_KEY_1", "dummy-test-key")
os.environ.setdefault("MEDEXTRACT_API_KEY", "")  # auth disabled by default in tests
os.environ.setdefault("MEDEXTRACT_RATE_LIMIT_PER_MINUTE", "1000")  # don't let tests trip the limiter

import pytest


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from src import api as api_module

    return TestClient(api_module.app)
