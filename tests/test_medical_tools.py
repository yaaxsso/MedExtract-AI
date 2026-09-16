"""
Tests for src/medical_tools.py — the four live medical lookups, plus the
TTL cache wrapping them. Every test mocks requests.get, so nothing here
makes a real network call.
"""
from unittest.mock import patch, MagicMock

import pytest

from src import medical_tools as mt


# ---------------------------------------------------------------------------
# condition_info_lookup (MedlinePlus)
# ---------------------------------------------------------------------------
MEDLINEPLUS_XML = b"""<?xml version="1.0"?>
<nlmSearchResult>
  <list>
    <document url="https://medlineplus.gov/asthma.html">
      <content name="title">Asthma &lt;b&gt;highlight&lt;/b&gt;</content>
      <content name="snippet">A chronic disease of the airways.</content>
    </document>
  </list>
</nlmSearchResult>
"""


def test_condition_info_lookup_found():
    mt.condition_info_lookup.cache_clear()
    mock_resp = MagicMock(content=MEDLINEPLUS_XML)
    mock_resp.raise_for_status.return_value = None
    with patch.object(mt.requests, "get", return_value=mock_resp) as mock_get:
        result = mt.condition_info_lookup("asthma")
    assert result["found"] is True
    assert result["results"][0]["title"] == "Asthma highlight"  # HTML tags stripped
    assert result["results"][0]["url"] == "https://medlineplus.gov/asthma.html"
    mock_get.assert_called_once()


def test_condition_info_lookup_not_found():
    mt.condition_info_lookup.cache_clear()
    empty_xml = b"<nlmSearchResult><list></list></nlmSearchResult>"
    mock_resp = MagicMock(content=empty_xml)
    mock_resp.raise_for_status.return_value = None
    with patch.object(mt.requests, "get", return_value=mock_resp):
        result = mt.condition_info_lookup("not_a_real_condition_xyz")
    assert result["found"] is False
    assert result["results"] == []


# ---------------------------------------------------------------------------
# medication_lookup (RxNorm)
# ---------------------------------------------------------------------------
def test_medication_lookup_found():
    mt.medication_lookup.cache_clear()
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {"idGroup": {"rxnormId": ["161"]}}
    with patch.object(mt.requests, "get", return_value=mock_resp):
        result = mt.medication_lookup("ibuprofen")
    assert result == {"found": True, "rxcui": "161", "name": "ibuprofen"}


def test_medication_lookup_not_found():
    mt.medication_lookup.cache_clear()
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {"idGroup": {}}
    with patch.object(mt.requests, "get", return_value=mock_resp):
        result = mt.medication_lookup("not_a_real_drug_xyz")
    assert result["found"] is False
    assert "not_a_real_drug_xyz" in result["message"]


# ---------------------------------------------------------------------------
# dosing_lookup (openFDA)
# ---------------------------------------------------------------------------
def test_dosing_lookup_found():
    mt.dosing_lookup.cache_clear()
    mock_resp = MagicMock(status_code=200)
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "results": [{"dosage_and_administration": ["Take 1 tablet by mouth every 6 hours."]}]
    }
    with patch.object(mt.requests, "get", return_value=mock_resp):
        result = mt.dosing_lookup("ibuprofen")
    assert result["found"] is True
    assert "Take 1 tablet" in result["dosage_text"]


def test_dosing_lookup_404():
    mt.dosing_lookup.cache_clear()
    mock_resp = MagicMock(status_code=404)
    with patch.object(mt.requests, "get", return_value=mock_resp):
        result = mt.dosing_lookup("not_a_real_drug_xyz")
    assert result["found"] is False


def test_dosing_lookup_truncates_long_text():
    mt.dosing_lookup.cache_clear()
    long_text = "x" * 1000
    mock_resp = MagicMock(status_code=200)
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {"results": [{"dosage_and_administration": [long_text]}]}
    with patch.object(mt.requests, "get", return_value=mock_resp):
        result = mt.dosing_lookup("something")
    assert len(result["dosage_text"]) == 500


# ---------------------------------------------------------------------------
# icd10_lookup (NLM Clinical Tables)
# ---------------------------------------------------------------------------
def test_icd10_lookup_filters_unsupported_specifiers():
    mt.icd10_lookup.cache_clear()
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    # data[3] is a list of [code, name] pairs, per the real API's response shape.
    mock_resp.json.return_value = [
        4, None, None,
        [
            ["F32.9", "Major depressive disorder, single episode, unspecified"],
            ["F33.9", "Major depressive disorder, recurrent, unspecified"],
            ["F32.1", "Major depressive disorder, single episode, moderate"],
        ],
    ]
    with patch.object(mt.requests, "get", return_value=mock_resp):
        result = mt.icd10_lookup("major depressive disorder")
    assert result["found"] is True
    codes = [c["code"] for c in result["codes"]]
    assert "F32.9" in codes
    assert "F33.9" not in codes  # "recurrent" filtered out
    assert "F32.1" not in codes  # "moderate" filtered out


def test_icd10_lookup_falls_back_when_every_result_has_a_specifier():
    mt.icd10_lookup.cache_clear()
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = [
        1, None, None,
        [["F33.9", "Major depressive disorder, recurrent, unspecified"]],
    ]
    with patch.object(mt.requests, "get", return_value=mock_resp):
        result = mt.icd10_lookup("major depressive disorder")
    assert result["found"] is True
    assert result["codes"][0]["code"] == "F33.9"
    assert "note" in result  # explains why an unfiltered result was returned


def test_icd10_lookup_not_found():
    mt.icd10_lookup.cache_clear()
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = [0, None, None, []]
    with patch.object(mt.requests, "get", return_value=mock_resp):
        result = mt.icd10_lookup("not_a_real_diagnosis_xyz")
    assert result["found"] is False


# ---------------------------------------------------------------------------
# TTL cache behavior (shared by all four functions above)
# ---------------------------------------------------------------------------
def test_cache_avoids_duplicate_calls_for_same_args():
    mt.medication_lookup.cache_clear()
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {"idGroup": {"rxnormId": ["161"]}}
    with patch.object(mt.requests, "get", return_value=mock_resp) as mock_get:
        mt.medication_lookup("ibuprofen")
        mt.medication_lookup("ibuprofen")
        mt.medication_lookup("ibuprofen")
    mock_get.assert_called_once()  # second and third calls served from cache


def test_cache_is_keyed_per_argument():
    mt.medication_lookup.cache_clear()
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {"idGroup": {"rxnormId": ["161"]}}
    with patch.object(mt.requests, "get", return_value=mock_resp) as mock_get:
        mt.medication_lookup("ibuprofen")
        mt.medication_lookup("acetaminophen")
    assert mock_get.call_count == 2  # different args -> different cache entries


def test_cache_expires_after_ttl(monkeypatch):
    mt.medication_lookup.cache_clear()
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {"idGroup": {"rxnormId": ["161"]}}

    fake_now = [1000.0]
    monkeypatch.setattr(mt.time, "monotonic", lambda: fake_now[0])

    with patch.object(mt.requests, "get", return_value=mock_resp) as mock_get:
        mt.medication_lookup("ibuprofen")
        fake_now[0] += mt._CACHE_TTL_SECONDS + 1  # advance past expiry
        mt.medication_lookup("ibuprofen")

    assert mock_get.call_count == 2  # cache entry had expired, so it re-fetched
