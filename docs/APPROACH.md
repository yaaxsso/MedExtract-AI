# MedExtract AI — Approach Document

## 1. Project Summary

MedExtract AI takes an unstructured clinical/patient note and uses an LLM (via a hosted API) to extract structured medical information into a fixed JSON schema (chief complaint, symptoms, diagnosis, medical history, medications, procedures, follow-up, summary, risk indicators, urgency). The system goes beyond pure extraction and **reasons toward a diagnosis and suggested medications** — but every step of that reasoning is verified live against real medical databases through MCP tool calls, not the model's memory and not a locally-built knowledge base. Extraction fields directly stated in the note remain grounded to their source sentence, exactly as before. The result is shown to the doctor through a **Streamlit interface styled like a prescription/report**, clearly marked as clinician-reviewed suggestions rather than a finalized, autonomous prescription.

## 2. Audience

**Doctors and medical staff** — the system is designed to help clinicians quickly track a patient's condition from unstructured notes (their own documentation, or a patient's self-report/journal entry) instead of re-reading everything by hand. The doctor-facing view is designed to look and feel familiar — like a prescription or clinical summary — rather than a technical JSON dump.

**Note on the dataset:** the available dataset (`clinical_notes.csv` + `patient_diaries.csv`, from Kaggle's "Patient Diaries and Clinical Notes Dataset") is mostly patient self-report / journal-style text focused on mood and depression, not full clinical encounters. This means fields like `procedures` will often correctly return `null` — that's expected behavior given the input, not a bug, and is treated as such throughout evaluation (Section 6).

## 3. Design Philosophy

Every output must be **checkable**, not just trusted:
- **Grounded extraction** — every directly-stated field is tagged with the sentence in the note that supports it, so a doctor can verify a claim in seconds instead of re-reading the note.
- **Live tool-verified reasoning, not a guess** — diagnosis, medication names, and dosing information are all checked against real medical databases at the moment of reasoning, via MCP tool calls (Section 5, Phase 3.5) — the model never relies on its own memory for medically consequential facts.
- **Suggestions for review, not a finalized prescription** — anything the system proposes (diagnosis, medication, dosing) is explicitly marked as a clinician-reviewed suggestion, especially dosing, which is shown as a standard reference value, never a personalized calculation.
- **Validation before anything is finalized** — malformed or schema-violating output is caught and retried automatically.
- **A familiar, prescription-style presentation** — the doctor never has to read raw JSON; the same validated, grounded data is rendered in a clean, clinical-looking layout they can scan the way they'd scan any other patient document.

---

## 4. Tech Stack

| Purpose | Tool / Library | Notes |
|---|---|---|
| Data handling | **pandas** | Reading/exploring `clinical_notes.csv` and `patient_diaries.csv` |
| LLM inference | **[Provider TBD — API-based, e.g. Claude/GPT/Gemini]** | Hosted API — chosen for stronger reasoning quality; local LLMs are out of scope for this project |
| Structured output guardrail | Provider's native JSON/structured-output mode | Baseline guarantee of parseable JSON |
| Schema validation | **Pydantic** | Models mirror the JSON schema exactly; used for both dev-time and runtime validation |
| API layer | **FastAPI** | Single extraction endpoint; also exposes the medical-verification and ICD-10 routes |
| Doctor-facing UI | **Streamlit** | Renders the validated JSON as a prescription/report-style view, not raw JSON |
| Diagnosis verification | **MedlinePlus Connect API** (NLM, free) | Live lookup of authoritative condition info to check the model's diagnosis reasoning against, via MCP |
| Medication verification | **RxNorm API** (NLM, free) | Confirms a suggested medication name is a real, correctly-named drug — the main defense against medication hallucination |
| Dosing reference | **DailyMed API** (NLM, free) | Pulls the real FDA label's standard dosing text — shown as a generic reference, never a calculated/personalized dose |
| ICD-10 code lookup (bonus) | **NLM Clinical Tables API** | Free, no API key; returns ICD-10-CM codes for a confirmed diagnosis |
| Tool-calling architecture | **MCP** (Model Context Protocol) — `mcp` Python SDK | One local MCP server exposing all four lookups (`condition_info_lookup`, `medication_lookup`, `dosing_lookup`, `icd10_lookup`) as tools; the extraction/reasoning agent acts as an MCP client. Falls back to direct code-side function calls if a full MCP setup isn't feasible in time |
| Version control | **Git + GitHub** | Repo already scaffolded (`README.md`, `docs/APPROACH.md`, `.gitignore`, `src/`, `notebooks/`, `data/`) |

**Why these choices fit the constraints:** the LLM runs via a hosted API for stronger reasoning quality — local LLMs are out of scope for this project. The note text does leave the machine during extraction/reasoning as a result; everything else (Pydantic, FastAPI, Streamlit, the MCP server) runs locally. All four medical lookups are live calls to free, authoritative NLM data sources — no local knowledge base or vector database is used anywhere in this project, keeping it clearly distinct from a RAG approach.

---

## 5. Pipeline Phases

### Phase 1 — Read & Understand the Dataset
Manually read a sample (20-30 notes) from both `clinical_notes.csv` and `patient_diaries.csv` using pandas. Note the difference in tone (third-person clinical vs. first-person diary) and build a running list of failure-prone patterns: negation, vague mood language, notes with zero clinical content, ambiguous severity. This list drives every phase after it.

### Phase 2 — Build a Small Labeled Evaluation Set
Hand-write the correct JSON for ~15-20 notes, pulling a mix from both files and covering the hard cases found in Phase 1. Stored as a simple JSON/CSV file alongside the notebooks. This is the ground truth everything else gets measured against.

### Phase 3 — Prompt Iteration Loop (V1 → V2 → V3 → Final)
- **V1 (baseline):** schema + plain instructions, no examples, called via the provider's API client. Run against the eval set, log every failure by category.
- **V2:** patch the worst offenders — explicit, repeated "return null if not stated, never infer without support" rules.
- **V3:** add 1-2 targeted few-shot examples for the hardest categories (negation, zero-clinical-content notes).
- **Final:** consolidate; write up *why* each change helped, backed by before/after evidence from the eval set.

**Grounded extraction** is built into the schema here: each field also carries a `source_sentence` (or `null`), giving doctors an instant way to verify any extracted claim.

### Phase 3.5 — Live Medical Verification via MCP Tools
The system reasons toward a diagnosis and suggested medications — but every claim is checked live against real medical data sources through MCP tool calls, not the model's memory and not a pre-built local knowledge base (that technique is used elsewhere in the broader project, so this part is deliberately built differently).

1. **Diagnosis reasoning, verified against MedlinePlus Connect:** when the model proposes a diagnosis, it calls `condition_info_lookup` to pull real, authoritative symptom/definition info for that condition live from NLM, and must show how the note's stated symptoms align with what the tool actually returned — not just assert a label.
2. **Medication naming, verified against RxNorm:** if the model suggests a medication, it calls `medication_lookup` to confirm the name is a real, correctly-spelled drug in RxNorm's database. A name that doesn't resolve is rejected/retried rather than shown to the doctor — this is the main structural defense against medication hallucination.
3. **Dosing, sourced from DailyMed (with explicit safety framing):** once a medication is confirmed real, `dosing_lookup` pulls the standard dosing text straight from the drug's real FDA label via DailyMed. The system never calculates or personalizes a dose — a note rarely contains the information (weight, kidney function, interactions) that would make a personalized dose safe to compute. The dosing shown is always labeled **"standard reference dose — not patient-specific, for clinician review"**, both in the JSON output and unmissably in the Streamlit UI.
4. **Confidence flag:** if any of these tool calls fail to resolve (e.g. no clear condition/medication match), the field is returned as low-confidence rather than filled in with a guess.

**Framing for the write-up:** the system reasons toward clinically useful suggestions, but nothing is asserted from the model's own memory alone — diagnosis, medication identity, and dosing are all checked live against real, authoritative medical data sources, and every suggestion is explicitly marked for clinician review.

### Phase 4 — Runtime Validation & Repair Loop
Distinct from Phase 3 — this runs every single time the system processes a note, not just during development:
1. The provider's native JSON/structured-output mode as a baseline guardrail.
2. **Pydantic** validates against the full schema.
3. On failure, re-prompt the model with the specific validation error and retry (1-2 attempts) via the same API call, before logging a hard failure.

### Phase 5 — API Endpoint
A **FastAPI** app exposing the extraction/reasoning endpoint (note text in → validated, tool-verified JSON out), as required by the project brief.

### Phase 6 — Evaluation
Run the Final prompt across the full labeled eval set (pandas + scikit-learn) and report **three separate measures**, not one blended score:
- **Content-field accuracy** — for extraction fields that usually have real values (symptoms, risk_indicators): standard accuracy against gold labels.
- **Null-integrity check** — for fields that are usually absent (procedures, follow_up): confirm the model correctly returns null when nothing is stated, and is correct on the rare cases when something is.
- **Verification-pass rate** — how often the diagnosis/medication/dosing suggestions actually pass their respective MCP tool checks on the first try vs. needing a retry or getting flagged low-confidence. This is a direct, measurable proxy for how well-grounded the reasoning is, not just whether the final label happens to be correct.

### Phase 7 — Bonus: ICD-10 Lookup via MCP
Placed after Phases 1-6 are stable, since it depends on a confirmed diagnosis already existing. This directly implements the brief's own suggested bonus, and runs on the same MCP server built in Phase 3.5.

1. `icd10_lookup` queries the **NLM Clinical Tables API** with the confirmed diagnosis text and returns matching ICD-10-CM codes + descriptions.
2. Exposed as an MCP tool alongside the three medical-verification tools — the extraction/reasoning agent calls it once a diagnosis is confirmed.
3. **Fallback:** if a full MCP setup isn't feasible in the timeline, call the lookup function directly in code (no agent/MCP layer) — same correctness guarantee, documented as a graceful simplification.

Given the dataset mostly yields a small, repetitive set of diagnoses (largely depression-related), this bonus is smaller in scope than it might sound.

### Phase 8 — Doctor-Facing Output: Streamlit Prescription-Style View
Instead of a doctor reading raw JSON, the validated, tool-verified result is rendered as a clean, familiar, **prescription/clinical-report-style page** in Streamlit.

1. **Input to this phase:** the final validated JSON from Phases 3-5, including the ICD-10 code (Phase 7) — nothing new is extracted here, this is purely a presentation layer.
2. **Layout, styled like a prescription pad / clinical summary:**
   - Header area: patient/note reference, date (from the note if available)
   - **Chief complaint & diagnosis** shown prominently, with the ICD-10 code and a link to the supporting MedlinePlus info
   - **Symptoms, medical history, medications, procedures, follow-up** laid out as labeled sections, in the order a doctor would expect to scan them
   - **Medication + dosing section clearly labeled "Suggested — for clinician review"**, with the standard reference dose shown alongside an explicit "not patient-specific" note
   - **Risk indicators / urgency** visually flagged (e.g. colored badge) so anything urgent is impossible to miss
   - Fields that are genuinely `null` are shown as "Not mentioned in note" rather than left blank
3. **Grounding surfaced in the UI:** each displayed value is clickable/hoverable to reveal either the source sentence (for extracted fields) or the verifying tool result (for diagnosis/medication/dosing) — bringing the "checkable, not just trusted" philosophy directly into what the doctor sees.
4. **Not a replacement for the API:** the FastAPI endpoint (Phase 5) remains the actual programmatic interface; Streamlit is a thin, separate front-end that calls it and renders the result.

---

## 6. Validating Safety, Not Just Accuracy

Tool verification (Phase 3.5) catches the model *fabricating* something that doesn't exist — a fake drug, a fake condition. It does not catch the model correctly naming something real but wrong for this case. Validation needs to be layered to cover both:

1. **Accuracy against the labeled eval set** (Phase 6) — is the diagnosis/medication suggestion actually correct against gold labels, not just well-formatted and source-cited.
2. **Verification-pass rate** (Phase 6) — how often suggestions pass their MCP tool checks on the first try vs. getting rejected/flagged — a direct, measurable signal of how well-grounded the reasoning is.
3. **Adversarial/edge-case testing** — deliberately include notes designed to be ambiguous or contradictory (conflicting symptoms, a condition that could plausibly be two different diagnoses, a mentioned medication that might interact with a suggested one) in the eval set, and check the system's behavior specifically on these, not just on the easy cases.
4. **Consistency checking** — run the same note through the system 2-3 times; if the diagnosis or medication suggestion changes between runs, that instability itself is a reportable finding, not something to hide by only showing the best run.
5. **Appropriate-uncertainty rate** — mark which eval-set notes are genuinely ambiguous/high-risk, then check whether the system correctly returns low-confidence or flags for review on those specific notes, rather than being confidently wrong. A system that's accurate on easy cases but confidently wrong on hard ones is more dangerous than one that's honest about uncertainty.
6. **Human review as a structural requirement, not a disclaimer** — every diagnosis, medication, and dose is presented as a suggestion for the doctor to confirm, built into the Streamlit UI itself (Phase 8), not a legal caveat at the bottom of the page. Tool verification reduces fabrication risk; it does not make the system safe to use unsupervised, and the design does not claim otherwise.

**Honest limitation to state directly in the write-up:** a short, unstructured note with no labs, physical exam, or full patient history cannot support a fully confident, personalized diagnosis or dose no matter how well-engineered the pipeline is. The goal of this validation approach is a system that is accurate where it can be and clearly uncertain where it can't — not a system that appears maximally confident at all times.

---

## 7. Build Order

1. Read & understand the dataset (Phase 1) — pandas
2. Build the labeled eval set (Phase 2)
3. Prompt iteration V1 → V2 → V3 → Final, with grounded extraction (Phase 3) — hosted LLM API
3.5. Set up the MCP server with the three medical-verification tools (Phase 3.5) — MedlinePlus Connect, RxNorm, DailyMed
4. Runtime validation & repair loop (Phase 4) — Pydantic
5. API endpoint (Phase 5) — FastAPI
6. Evaluation — three-way split metrics (Phase 6) — pandas/scikit-learn
7. ICD-10 bonus, added to the same MCP server (Phase 7) — NLM Clinical Tables API
8. Doctor-facing Streamlit view, styled like a prescription (Phase 8) — Streamlit, calling the Phase 5 API

---

## 8. What Makes This Approach Defensible

- Every design choice ties back to a real requirement: grounded extraction → "no hallucination" on stated facts; live MCP-verified reasoning → allowing the model to diagnose/suggest medication without opening the door to unsupported guesses; the explicit "not patient-specific" dosing caveat → a genuine safety boundary, not a formality; validation/repair → schema compliance; split evaluation → honest measurement of both accuracy and groundedness; the dedicated safety-validation methodology (Section 6) → separating "does it fabricate" from "is it actually correct," which is a distinction most similar projects skip.
- The reasoning approach is deliberately **not RAG** — no local knowledge base, no embeddings, no vector database — every medical fact is checked live against a real, authoritative external source via MCP tool calls. This keeps the technique clearly distinct from a teammate's RAG-based work on a different part of the project.
- Local LLMs are intentionally out of scope for this project; the trade-off (note text leaving the machine during reasoning) is stated plainly rather than glossed over.
- The doctor-facing audience is stated explicitly, along with an honest account of where the dataset falls short of full clinical documentation (limited procedure info) — framed as a scoping decision, not an oversight.
- The prescription-style Streamlit view directly serves the stated audience, with every diagnosis, medication, and dose clearly marked as a clinician-reviewed suggestion — never presented as a finalized, autonomous prescription.
- The core pipeline (Phases 1-6) is fully functional and gradeable without the UI or the ICD-10 bonus — both are additive, not dependencies.
