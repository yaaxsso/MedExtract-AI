"""
MedExtract AI — Phase 5 API.

Wraps the full pipeline (extraction -> validation -> diagnosis/medication/dosing/ICD-10
reasoning, all live-verified against real medical data sources) behind a single FastAPI
endpoint. Run with:

    uvicorn src.api:app --reload

Then POST to http://127.0.0.1:8000/extract with JSON body: {"text": "..."}

Phase 3.5 (diagnosis/medication/dosing/ICD-10 reasoning) talks to its four medical
lookups over a real MCP server (src/mcp_server.py), via src/mcp_client.py. If the MCP
subprocess can't be started for some reason (mcp package missing, sandboxed
environment, etc.), reason_diagnosis falls back to calling the same underlying
functions directly in-process — the "graceful simplification" fallback described in
docs/APPROACH.md — and says so in the result.
"""
import os
import re
import json
import asyncio
import logging
from typing import Optional, List, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
from google import genai
from google.genai.errors import ClientError

from src.mcp_client import mcp_session
from src.medical_tools import (
    condition_info_lookup,
    medication_lookup,
    dosing_lookup,
    icd10_lookup,
)

logger = logging.getLogger("medextract.api")

# ---------------------------------------------------------------------------
# Setup: API keys, client pool, fallback wrapper
# ---------------------------------------------------------------------------
load_dotenv()

_api_keys = [os.environ.get(f"GEMINI_API_KEY_{i}") for i in (1, 2, 3)]
_api_keys = [k for k in _api_keys if k]
if not _api_keys and os.environ.get("GEMINI_API_KEY"):
    _api_keys = [os.environ["GEMINI_API_KEY"]]
if not _api_keys:
    raise RuntimeError(
        "No Gemini API key found. Set GEMINI_API_KEY_1 (and optionally _2, _3) "
        "or GEMINI_API_KEY in your .env file."
    )

_clients = [genai.Client(api_key=k) for k in _api_keys]
MODEL_NAME = "gemini-3.5-flash-lite"
_current_client_index = 0


def generate_with_fallback(**kwargs):
    global _current_client_index
    last_error = None
    for attempt in range(len(_clients)):
        idx = (_current_client_index + attempt) % len(_clients)
        try:
            response = _clients[idx].models.generate_content(**kwargs)
            _current_client_index = idx
            return response
        except ClientError as e:
            if e.code == 429:
                last_error = e
                continue
            raise
    raise last_error


async def generate_with_fallback_async(**kwargs):
    """Async counterpart to generate_with_fallback, used by the Phase 3.5 reasoning
    call so it can pass a live MCP ClientSession as a tool (MCP support in the
    google-genai SDK only works through the async client)."""
    global _current_client_index
    last_error = None
    for attempt in range(len(_clients)):
        idx = (_current_client_index + attempt) % len(_clients)
        try:
            response = await _clients[idx].aio.models.generate_content(**kwargs)
            _current_client_index = idx
            return response
        except ClientError as e:
            if e.code == 429:
                last_error = e
                continue
            raise
    raise last_error


def parse_json_response(raw_text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text.strip())
    return json.loads(cleaned)


# ---------------------------------------------------------------------------
# Phase 3 — Extraction
# ---------------------------------------------------------------------------
FINAL = """
    Role: You are a Therapist extracting structured data from clinical notes.

    Rules:
    - Only extract what is explicitly stated. Never infer or guess.
    - Always fill in "summary" with a one-sentence summary, even if nothing else is found.
    - Always set "urgency" to "low", "moderate", or "high" — never leave it blank.
    - Use null for any field with no information. Do not use empty arrays or placeholder objects.

    The output must be a valid JSON object with exactly these fields:
    {  "chief_complaint": null,
            "symptoms": null,
            "diagnosis": null,
            "medical_history": null,
            "medications": null,
            "procedures": null,
            "follow_up": null,
            "summary": null,
            "risk_indicators": null,
            "urgency": null
        }

    Example:
    Note: "Patient feels sad; a referral to a psychiatrist may be necessary."
    Output:
    {
    "chief_complaint": null,
    "symptoms": ["sadness"],
    "diagnosis": null,
    "medical_history": null,
    "medications": null,
    "procedures": null,
    "follow_up": "referral to psychiatrist recommended",
    "summary": "Patient reports sadness; psychiatric referral suggested.",
    "risk_indicators": null,
    "urgency": "moderate"
    }

    Now extract from this note, following the exact same format:
"""


def extract_final(note_text: str) -> str:
    response = generate_with_fallback(
        model=MODEL_NAME, contents=f"{FINAL}\n\nText:\n{note_text}"
    )
    return response.text


# ---------------------------------------------------------------------------
# Phase 4 — Validation & repair
# ---------------------------------------------------------------------------
class ExtractionSchema(BaseModel):
    chief_complaint: Optional[str] = None
    symptoms: Optional[List[str]] = None
    diagnosis: Optional[str] = None
    medical_history: Optional[str] = None
    medications: Optional[List[str]] = None
    procedures: Optional[List[str]] = None
    follow_up: Optional[str] = None
    summary: str
    risk_indicators: Optional[str] = None
    urgency: Literal["low", "moderate", "high"]


def validate_and_repair(raw_json_text, schema_class, generate_fn, max_retries=2):
    for attempt in range(max_retries + 1):
        try:
            data = json.loads(raw_json_text)
            validated = schema_class(**data)
            return validated.model_dump(), True
        except Exception as e:
            if attempt == max_retries:
                return {"error": str(e), "raw_output": raw_json_text}, False
            raw_json_text = generate_fn(
                f"Your previous output failed validation with this error: {e}\n"
                f"Previous output: {raw_json_text}\n"
                f"Please fix it and return only valid JSON matching the required schema."
            ).text


# ---------------------------------------------------------------------------
# Phase 3.5 — Live medical verification, over a real MCP server
# ---------------------------------------------------------------------------
# The four lookups themselves (condition_info_lookup, medication_lookup,
# dosing_lookup, icd10_lookup) live in src/medical_tools.py and are imported
# above. src/mcp_server.py registers those same functions as MCP tools; this
# module's own imported references are used only as the direct-call fallback
# in reason_diagnosis below, if the MCP subprocess can't be reached.


class ICD10Candidate(BaseModel):
    code: str
    description: str


class DiagnosisReasoningResult(BaseModel):
    diagnosis: Optional[str] = None
    icd10_candidates: Optional[List[ICD10Candidate]] = None
    suggested_medications: Optional[List[str]] = None
    medication_selection_note: Optional[str] = None
    unverified_medications_mentioned: Optional[List[str]] = None
    dosing_reference: Optional[str] = None
    confidence: Literal["low", "moderate", "high"]
    reasoning_notes: str


DIAGNOSIS_PROMPT = """You are reasoning about a possible diagnosis based on extracted symptoms or note content.

Rules:
- Before stating any diagnosis, call condition_info_lookup_tool to verify it against real medical information.
- Before suggesting any medication, call medication_lookup_tool to confirm it's a real medication.
  - If a mentioned medication is NOT found/verified, do NOT ask the user clarifying questions.
    Instead, put its name in "unverified_medications_mentioned" and do not include it in "suggested_medications".
- MEDICATION SUGGESTION IS GATED BY CONFIDENCE, SEPARATELY FROM THE DIAGNOSIS-WORDING GATE BELOW:
  suggesting a real medication is a higher-stakes claim than naming a diagnosis, so it is held to
  its own, stricter bar. At "low" or "moderate" confidence, "suggested_medications" MUST be null
  and "dosing_reference"/"medication_selection_note" MUST also be null — regardless of what
  medications might otherwise seem reasonable for the diagnosis. Only at "high" confidence may you
  proceed to the medication-scope rule below. This is deliberately stricter than the diagnosis-
  wording gate: a note can carry enough evidence for a generic "unspecified" descriptor while still
  not being enough basis to name a specific drug.
- MEDICATION SCOPE, DELIBERATELY LIMITED: suggest at most 2 medications, both well-established
  first-line options for the same drug class as the diagnosis (e.g. two common SSRIs for
  depression) — never a large list. Do not rank or imply one is preferred over the other.
  Always fill "medication_selection_note" with a short sentence stating that choosing between
  them depends on patient-specific factors not present in this note (prior response, interactions,
  side-effect tolerance, comorbidities) and is a clinician decision. This is intentional: a short
  unstructured note cannot support the system doing that selection.
- If you suggest verified medication(s), call dosing_lookup_tool for each. Build "dosing_reference"
  by concatenating, for each medication, "<DrugName>: " followed by its dosage_text exactly as
  returned (do not paraphrase, trim, or invent it), then " (not patient-specific, for clinician
  review)." — so two suggested medications produce two clearly drug-labeled blocks, never merged
  without saying which text belongs to which drug.
  - FDA labels often cover several indications in one dosing section (e.g. depression, OCD, panic
    disorder, combination therapy for bipolar depression) and dosage_text may include all of them.
    If ANY of the dosage_text you're including appears to reference an indication OTHER than the
    diagnosis you're suggesting the medication for, prepend this exact line ONCE at the very start
    of the whole "dosing_reference" field (not once per medication): "NOTE: this label excerpt
    covers multiple indications; confirm which dosing line applies to this diagnosis before use. "
    Do not try to strip out the other-indication text yourself — flagging it is the goal, not
    editing the medical text itself.
- Once a diagnosis is confirmed via condition_info_lookup_tool, call icd10_lookup_tool using a
  plain, general clinical term for the underlying condition (e.g. "depression", "anxiety") — NOT
  the deliberately vague/unspecified wording you use in the "diagnosis" field itself under the
  confidence-gating rule below. The ICD-10 search is a literal text match, not a clinical one: a
  vague phrase like "Depressive symptoms, unspecified" can pull back unrelated symptom-only codes
  (e.g. R45.x "symptoms and signs involving emotional state") instead of real depressive-disorder
  codes (F32.x), just because the word "symptoms" matched. Copy ALL the candidate codes the tool
  returns (up to 3) into "icd10_candidates" as {code, description} pairs — do not narrow it down to
  a single code yourself. Coding to one final code from a short note is a clinician/coder decision,
  not something this system should silently decide.
- Never state a diagnosis, medication, dose, or code without calling the matching tool first.

- GUARD AGAINST FALSE POSITIVES: if the input describes only positive, neutral, or coping content
  (e.g. happiness, hope, laughing with a friend, accomplishing tasks) with NO negative mood or
  symptom language, you MUST set "diagnosis", "icd10_candidates", "suggested_medications", and
  "dosing_reference" to null, "confidence" to "low", and explicitly state in reasoning_notes that
  no negative indicators were present.

- GUARD AGAINST OVER-SPECIFIC DIAGNOSES FROM VAGUE EVIDENCE — DIAGNOSIS NAME IS GATED BY CONFIDENCE:
  - "low" confidence (0-1 indicators): "diagnosis" MUST be null. Do not name any condition.
  - "moderate" confidence (2 indicators): "diagnosis" MUST be a general/unspecified descriptor only
    (e.g. "Depressive symptoms, unspecified", "Mood disturbance, unspecified") — NEVER a named
    DSM-style disorder (e.g. "Major Depressive Disorder", "Bipolar Disorder", "Generalized Anxiety
    Disorder", "Adjustment Disorder"). Two vague self-reported phrases ("feeling low", "doesn't want
    to talk to anyone") are not sufficient evidence for a formally-named disorder, even though they
    are sufficient to flag depressive symptoms worth a clinician's attention.
  - "high" confidence (3+ indicators, or the text itself uses an explicit diagnosis word / states a
    clinical referral) may use a specific named disorder — but only if the text contains evidence of
    THAT diagnosis's actual defining features, not just any negative-sounding phrase. "Fluctuating
    moods" alone, with no mania/hypomania language, is still not evidence for Bipolar Disorder at
    any confidence level — use "Mood disturbance, unspecified" instead, and never suggest a
    narrow-therapeutic-window medication (e.g. lithium) from weak, nonspecific evidence.
  - DO NOT INVENT EPISODE/SEVERITY QUALIFIERS: never describe a condition as "recurrent",
    "severe", "chronic", or similar unless the note explicitly documents that history (e.g. past
    episodes, stated duration/severity). A first-time or undated mention must map to an
    "unspecified" episode/severity level, both in "diagnosis" and in the diagnosis text you pass to
    icd10_lookup_tool — this affects which candidate codes the tool returns, so getting the
    diagnosis text right here is what keeps the ICD-10 candidates honest.

- CONFIDENCE CALIBRATION: count the independent negative symptom/clinical indicators explicitly
  present in the text. Set confidence: 0 indicators -> diagnosis null, confidence "low";
  1 indicator -> "low"; 2 indicators -> "moderate"; 3+ indicators or an explicit diagnosis
  word/clinical referral -> "high". State the count and which indicators you found in
  reasoning_notes.
- Always respond with the structured output — never ask the user a clarifying question instead.
"""


async def _run_reasoning(input_text: str, tools: list) -> str:
    """Run the diagnosis-reasoning prompt with a given tools list — either a live MCP
    ClientSession (`[session]`) or the direct-call fallback functions — retrying once
    with tools disabled if the model doesn't return final text."""
    # NOTE: config is passed as a plain dict here, not types.GenerateContentConfig(...).
    # google-genai's AsyncModels.generate_content calls config.model_copy(deep=True)
    # internally, but only on the GenerateContentConfig-object path — and a live MCP
    # ClientSession holds an internal asyncio.Future that can't be deep-copied, which
    # crashes with "TypeError: cannot pickle '_asyncio.Future' object". The dict path
    # skips that deep-copy. This is a confirmed upstream bug (googleapis/python-genai
    # #2669, fix pending in PR #2723) — worth switching back to the object form once
    # that fix ships, but for now the dict form is the reliable one when `tools`
    # contains an MCP session.
    response = await generate_with_fallback_async(
        model=MODEL_NAME,
        contents=f"{DIAGNOSIS_PROMPT}\n\nInput: {input_text}",
        config={
            "tools": tools,
            "response_mime_type": "application/json",
            "response_schema": DiagnosisReasoningResult,
        },
    )
    if response.text is not None:
        return response.text

    # response.text came back None: the turn ended without synthesized text, most likely because
    # the model got stuck in the tool-calling loop (hit the SDK's automatic function-calling cap
    # without finalizing), hit a token limit mid-reasoning, or was blocked by a safety filter.
    # Surface the real reason instead of letting a downstream json.loads(None) fail with a
    # generic, undebuggable message.
    finish_reason = None
    safety_ratings = None
    try:
        candidate = response.candidates[0]
        finish_reason = getattr(candidate, "finish_reason", None)
        safety_ratings = getattr(candidate, "safety_ratings", None)
    except (AttributeError, IndexError, TypeError):
        pass

    # One retry: ask explicitly for the final structured answer with no further tool calls, in
    # case this was tool-loop exhaustion rather than a hard block.
    retry_response = await generate_with_fallback_async(
        model=MODEL_NAME,
        contents=(
            f"{DIAGNOSIS_PROMPT}\n\nInput: {input_text}\n\n"
            "Your previous attempt did not finish with a final answer. Do not call any more "
            "tools — using only what you already know from this input and any general medical "
            "knowledge you're confident in, return the best structured JSON answer now, erring "
            "toward lower confidence if you couldn't complete tool verification."
        ),
        config={
            "response_mime_type": "application/json",
            "response_schema": DiagnosisReasoningResult,
        },
    )
    if retry_response.text is not None:
        return retry_response.text

    raise RuntimeError(
        f"reason_diagnosis: model returned no text on first attempt or retry "
        f"(finish_reason={finish_reason}, safety_ratings={safety_ratings})"
    )


async def reason_diagnosis(input_text: str) -> tuple[str, str]:
    """Reason toward a diagnosis, verifying every claim through the MCP medical-tools
    server (src/mcp_server.py, via src/mcp_client.py).

    Returns (raw_json_text, mcp_status): mcp_status is "mcp" on the normal path, or
    "direct_fallback" if the MCP subprocess couldn't be reached — in which case the
    same underlying lookup functions are still called directly in-process, matching
    the fallback documented in docs/APPROACH.md. Only a failure to establish the MCP
    session triggers the fallback; a genuine reasoning error (e.g. the model failing
    to return final text) propagates normally rather than silently retrying twice.
    """
    session_cm = mcp_session()
    try:
        session = await session_cm.__aenter__()
    except Exception as e:
        logger.warning(
            "MCP session unavailable (%s: %s) — falling back to direct in-process "
            "tool calls for this request.",
            type(e).__name__, e,
        )
        raw_text = await _run_reasoning(
            input_text,
            tools=[condition_info_lookup, medication_lookup, dosing_lookup, icd10_lookup],
        )
        return raw_text, "direct_fallback"

    try:
        raw_text = await _run_reasoning(input_text, tools=[session])
        return raw_text, "mcp"
    finally:
        await session_cm.__aexit__(None, None, None)


# ---------------------------------------------------------------------------
# Combined pipeline
# ---------------------------------------------------------------------------
async def process_note(note_text: str) -> dict:
    # Phase 3 extraction and its validation/repair loop don't use MCP tools and stay
    # synchronous under the hood; run them off the event loop so a slow extraction call
    # doesn't block other concurrent requests.
    raw_extraction = await asyncio.to_thread(extract_final, note_text)
    gen_fn = lambda p: generate_with_fallback(model=MODEL_NAME, contents=p)
    extraction_result, extraction_ok = await asyncio.to_thread(
        validate_and_repair, raw_extraction, ExtractionSchema, gen_fn
    )

    try:
        raw_reasoning, mcp_status = await reason_diagnosis(note_text)
        reasoning_result = json.loads(raw_reasoning)
        reasoning_result["mcp_status"] = mcp_status
        reasoning_ok = True
    except Exception as e:
        reasoning_result = {"error": str(e)}
        reasoning_ok = False

    return {
        "note_text": note_text,
        "extraction": extraction_result,
        "extraction_valid": extraction_ok,
        "diagnosis_reasoning": reasoning_result,
        "reasoning_valid": reasoning_ok,
    }


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="MedExtract AI")


class NoteInput(BaseModel):
    text: str


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_NAME, "api_keys_loaded": len(_api_keys)}


@app.post("/extract")
async def extract_note(note: NoteInput):
    if not note.text or not note.text.strip():
        raise HTTPException(status_code=400, detail="`text` must not be empty.")
    try:
        return await process_note(note.text)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Pipeline error: {e}")