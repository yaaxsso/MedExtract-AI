"""
MedExtract AI — Phase 3.5 medical-verification lookups.

Plain, dependency-light implementations of the four live medical lookups:
condition_info_lookup (MedlinePlus Connect), medication_lookup (RxNorm),
dosing_lookup (DailyMed via openFDA), icd10_lookup (NLM Clinical Tables).

This module has exactly one job: wrap those four external calls. It knows
nothing about MCP or about Gemini. Two things import it:
  - src/mcp_server.py registers each function as an MCP tool.
  - src/api.py imports them directly as a fallback path, used only if the
    MCP subprocess can't be started (see src/mcp_client.py) — matching the
    "graceful simplification" fallback described in docs/APPROACH.md.

Keeping the implementation here (instead of duplicating it in both places)
means the MCP path and the fallback path are guaranteed to behave identically.
"""
import re
import xml.etree.ElementTree as ET

import requests


def clean_html(text):
    if text is None:
        return None
    return re.sub(r"<[^>]+>", "", text).strip()


def condition_info_lookup(condition_text: str, max_results: int = 1) -> dict:
    """Look up authoritative medical info about a condition from MedlinePlus. Use this to verify a diagnosis before stating it."""
    url = "https://wsearch.nlm.nih.gov/ws/query"
    params = {"db": "healthTopics", "term": condition_text, "rettype": "brief"}
    response = requests.get(url, params=params, timeout=5)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    results = []
    for doc in root.findall(".//document")[:max_results]:
        title_el = doc.find(".//content[@name='title']")
        summary_el = doc.find(".//content[@name='snippet']")
        results.append({
            "title": clean_html(title_el.text if title_el is not None else None),
            "summary": clean_html(summary_el.text if summary_el is not None else None),
            "url": doc.attrib.get("url"),
        })
    return {"found": bool(results), "results": results}


def medication_lookup(drug_name: str) -> dict:
    """Verify a medication name is real before suggesting it. If found is False, do not suggest that medication."""
    url = "https://rxnav.nlm.nih.gov/REST/rxcui.json"
    params = {"name": drug_name}
    response = requests.get(url, params=params, timeout=5)
    response.raise_for_status()
    data = response.json()
    rxcui_list = data.get("idGroup", {}).get("rxnormId")
    if not rxcui_list:
        return {"found": False, "message": f"{drug_name!r} is not a recognized medication in RxNorm"}
    return {"found": True, "rxcui": rxcui_list[0], "name": drug_name}


def dosing_lookup(drug_name: str) -> dict:
    """Get standard FDA label dosing text for an already-verified medication. Generic reference only, never a personalized dose."""
    url = "https://api.fda.gov/drug/label.json"
    params = {"search": f'openfda.generic_name:"{drug_name}"', "limit": 1}
    response = requests.get(url, params=params, timeout=5)
    if response.status_code == 404:
        return {"found": False, "message": f"No FDA label found for {drug_name!r}"}
    response.raise_for_status()
    data = response.json()
    if not data.get("results"):
        return {"found": False, "message": f"No FDA label found for {drug_name!r}"}
    label = data["results"][0]
    dosage_text = label.get("dosage_and_administration", [None])[0]
    if not dosage_text:
        return {"found": True, "dosage_text": None, "note": "Label found but no dosage section available."}
    return {
        "found": True,
        "dosage_text": dosage_text[:500],
        "note": "Standard reference dose from FDA label — not patient-specific, for clinician review.",
    }


def icd10_lookup(diagnosis_text: str, max_results: int = 3) -> dict:
    """Look up official ICD-10-CM candidate codes for a confirmed diagnosis. Returns up to
    max_results candidates — keep all of them, do not narrow to one yourself. Never state a
    code from memory."""
    url = "https://clinicaltables.nlm.nih.gov/api/icd10cm/v3/search"
    # search a bit wider than max_results so there's still enough left after filtering below
    params = {"sf": "code,name", "terms": diagnosis_text, "maxList": max(max_results * 3, 8)}
    response = requests.get(url, params=params, timeout=5)
    response.raise_for_status()
    data = response.json()
    all_results = [{"code": c, "description": n} for c, n in data[3]]
    if not all_results:
        return {"found": False, "message": f"No ICD-10 code found for {diagnosis_text!r}"}

    # A short unstructured note essentially never documents episode count, remission status,
    # severity, or reproductive/perinatal context. NLM's search is a plain text match, not a
    # clinical one — it will happily return "recurrent", "in remission", severity-graded
    # ("mild"/"moderate"/"severe"), or population-specific ("postpartum", "neonatal") codes just
    # because the words in diagnosis_text overlap with the broader condition name. Filter those
    # out here, deterministically, rather than relying on the model to apply that judgment after
    # the fact on results it's also been told to keep in full.
    unsupported_specifiers = (
        "recurrent", "in remission", "in partial remission", "in full remission",
        "mild", "moderate", "severe",
        "postpartum", "peripartum", "puerperal", "antepartum",
        "neonatal", "perinatal", "in pregnancy", "newborn",
    )
    filtered = [r for r in all_results if not any(s in r["description"].lower() for s in unsupported_specifiers)]
    if filtered:
        return {"found": True, "codes": filtered[:max_results]}
    return {
        "found": True,
        "codes": all_results[:max_results],
        "note": (
            "Every match for this term specifies episode, remission, severity, or "
            "reproductive/perinatal detail (e.g. 'recurrent', 'in remission', 'moderate', "
            "'postpartum') that this note likely does not document. State this explicitly in "
            "reasoning_notes rather than presenting these codes as clean matches."
        ),
    }