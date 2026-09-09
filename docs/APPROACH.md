# MedExtract AI — Approach Document

## 1. Project Summary

MedExtract AI takes an unstructured clinical/patient note and uses a local LLM (via Ollama) to extract only the information explicitly stated into a fixed JSON schema (chief complaint, symptoms, diagnosis, medical history, medications, procedures, follow-up, summary, risk indicators, urgency). The system must never invent, diagnose, or prescribe — extraction only, with everything traceable back to what's actually written. The extracted data is then shown to the doctor through a **Streamlit interface styled like a prescription/report**, not as raw JSON.

## 2. Audience

**Doctors and medical staff** — the system is designed to help clinicians quickly track a patient's condition from unstructured notes (their own documentation, or a patient's self-report/journal entry) instead of re-reading everything by hand. The doctor-facing view is designed to look and feel familiar — like a prescription or clinical summary — rather than a technical JSON dump.

**Note on the dataset:** the available dataset (`clinical_notes.csv` + `patient_diaries.csv`, from Kaggle's "Patient Diaries and Clinical Notes Dataset") is mostly patient self-report / journal-style text focused on mood and depression, not full clinical encounters. This means fields like `medications`, `procedures`, and `follow_up` will often correctly return `null` — that's expected behavior given the input, not a bug, and is treated as such throughout evaluation (Section 7).

## 3. Design Philosophy

Every output must be **checkable**, not just trusted:
- **Grounded extraction** — every field is tagged with the sentence in the note that supports it, so a doctor can verify a claim in seconds instead of re-reading the note.
- **Validation before anything is finalized** — malformed or schema-violating output is caught and retried automatically.
- **A familiar, prescription-style presentation** — the doctor never has to read raw JSON; the same validated, grounded data is rendered in a clean, clinical-looking layout they can scan the way they'd scan any other patient document.

---

## 4. Tech Stack

| Purpose | Tool / Library | Notes |
|---|---|---|
| Data handling | **pandas** | Reading/exploring `clinical_notes.csv` and `patient_diaries.csv` |
| LLM inference | **Ollama** (Python client: `ollama` package, or raw REST calls to `localhost:11434`) | Local, no data leaves the machine |
| Primary model | **`qwen2.5:7b-instruct`** | Good instruction-following for its size; fits comfortably on an M1 Air. Fallback to a larger Ollama model (e.g. `qwen2.5:14b-instruct`) only if a persistent failure category survives V2/V3 |
| Structured output guardrail | Ollama's **`format: json`** mode | Baseline guarantee of parseable JSON (not schema-correctness) |
| Schema validation | **Pydantic** | Models mirror the JSON schema exactly; used for both dev-time and runtime validation |
| API layer | **FastAPI** | Single extraction endpoint; also a natural fit for later exposing the ICD-10 bonus route |
| Doctor-facing UI | **Streamlit** | Renders the validated JSON as a prescription/report-style view, not raw JSON |
| Evaluation | **pandas + scikit-learn** (`classification_report`, custom null-integrity check) | Two separate metrics, not one blended score (Section 7) |
| ICD-10 bonus — code source | **NLM Clinical Tables API** (`clinicaltables.nlm.nih.gov`) | Free, no API key; returns ICD-10-CM codes for a given diagnosis text |
| ICD-10 bonus — agent step | **MCP** (Model Context Protocol) — `mcp` Python SDK | Local MCP server exposes `icd10_lookup` (which internally calls the API); the extraction agent acts as an MCP client. Falls back to a direct code-side function call if a full MCP setup isn't feasible in time |
| Version control | **Git + GitHub** | Repo already scaffolded (`README.md`, `docs/APPROACH.md`, `.gitignore`, `src/`, `notebooks/`, `data/`) |

**Why these choices fit the constraints:** the core pipeline (Ollama, Pydantic, FastAPI) runs entirely locally — no cloud API calls, no API keys, no cost, and nothing about the note itself leaves the machine, which matters for a *medical* project's privacy story and for staying within an M1 Air's resources. The one exception is the ICD-10 bonus, which calls a free public API for the diagnosis code lookup (see Phase 7) — everything else stays local.

---

## 5. Pipeline Phases

### Phase 1 — Read & Understand the Dataset
Manually read a sample (20-30 notes) from both `clinical_notes.csv` and `patient_diaries.csv` using pandas. Note the difference in tone (third-person clinical vs. first-person diary) and build a running list of failure-prone patterns: negation, vague mood language, notes with zero clinical content, ambiguous severity. This list drives every phase after it.

### Phase 2 — Build a Small Labeled Evaluation Set
Hand-write the correct JSON for ~15-20 notes, pulling a mix from both files and covering the hard cases found in Phase 1. Stored as a simple JSON/CSV file alongside the notebooks. This is the ground truth everything else gets measured against.

### Phase 3 — Prompt Iteration Loop (V1 → V2 → V3 → Final)
- **V1 (baseline):** schema + plain instructions, no examples, called via the `ollama` Python client. Run against the eval set, log every failure by category.
- **V2:** patch the worst offenders — explicit, repeated "return null if not stated, never infer severity, never guess medications" rules.
- **V3:** add 1-2 targeted few-shot examples for the hardest categories (negation, zero-clinical-content notes).
- **Final:** consolidate; write up *why* each change helped, backed by before/after evidence from the eval set.

**Grounded extraction** is built into the schema here: each field also carries a `source_sentence` (or `null`), giving doctors an instant way to verify any extracted claim.

### Phase 4 — Runtime Validation & Repair Loop
Distinct from Phase 3 — this runs every single time the system processes a note, not just during development:
1. Ollama's `format: json` mode as a baseline guardrail.
2. **Pydantic** validates against the full schema.
3. On failure, re-prompt the model with the specific validation error and retry (1-2 attempts) via the same `ollama` client call, before logging a hard failure.

### Phase 5 — API Endpoint
A **FastAPI** app exposing a single extraction endpoint (note text in → validated JSON out), as required by the project brief.

### Phase 6 — Evaluation
Run the Final prompt across the full labeled eval set (pandas + scikit-learn) and report **two separate numbers**, not one blended score:
- **Content-field accuracy** — for fields that usually have real values (symptoms, diagnosis, risk_indicators): standard accuracy against gold labels.
- **Null-integrity check** — for fields that are usually absent (medications, procedures, follow_up): confirm the model correctly returns null when nothing is stated, and is correct on the rare cases when something is.

### Phase 7 — Bonus: ICD-10 Lookup via MCP + API
Placed after Phases 1-6 are stable, since it depends on a confirmed diagnosis already existing. This directly implements the brief's own suggested bonus ("turn it into an agent with an MCP/tool call for ICD-10 lookup").

1. **Code source: public ICD-10 API** — the **NLM Clinical Tables API** (`clinicaltables.nlm.nih.gov`, free, no key required) is queried with the diagnosis text and returns matching ICD-10-CM codes + descriptions. No local code table to download/maintain.
2. **Lookup function:** a small Python function `icd10_lookup(diagnosis_text)` that calls the API and returns the top 1-3 candidate codes, with basic error handling for network failures/timeouts.
3. **Wrap it as an MCP server:** using Python's `mcp` SDK, expose `icd10_lookup` as a proper MCP tool (name, description, input/output schema), running as a small local server process — the tool itself just happens to call an external API internally.
4. **Agent as MCP client:** after a diagnosis is extracted and validated, the extraction agent connects to the local MCP server as a client, sees `icd10_lookup` is available, and calls it — the model decides to invoke the tool, same as standard tool-calling, through the standardized MCP interface.
5. **Result attached to output:** the returned code + description get added to the final JSON as `icd10_codes`, tied to the diagnosis. The model never recalls a code from memory — only what the API actually returns.
6. **Fallback:** if setting up a full MCP server isn't feasible in the timeline, call `icd10_lookup()` directly in code right after extraction (no agent/MCP layer) — same correctness guarantee, documented as a graceful simplification rather than a failure. If the API is unreachable at demo time, log the failure gracefully rather than blocking the rest of the pipeline.

**Trade-off worth noting:** this is the one point in the pipeline that isn't fully local — the diagnosis text (not the full note) is sent to a public lookup service to get a code back. Worth a one-line acknowledgment in the write-up, since every other phase in this project is deliberately local-only.

Given the dataset mostly yields a small, repetitive set of diagnoses (largely depression-related), this bonus is smaller in scope than it might sound.

### Phase 8 — Doctor-Facing Output: Streamlit Prescription-Style View
This is the differentiator replacing voice input — instead of a doctor reading raw JSON, the validated, grounded extraction result is rendered as a clean, familiar, **prescription/clinical-report-style page** in Streamlit.

1. **Input to this phase:** the final validated JSON from Phase 4/5 (plus the ICD-10 code from Phase 7, if present) — nothing new is extracted here, this is purely a presentation layer.
2. **Layout, styled like a prescription pad / clinical summary:**
   - Header area: patient/note reference, date (from the note if available)
   - **Chief complaint & diagnosis** shown prominently, with the ICD-10 code next to the diagnosis if the bonus ran
   - **Symptoms, medical history, medications, procedures, follow-up** laid out as labeled sections, in the order a doctor would expect to scan them — matching the visual rhythm of a real prescription/summary sheet rather than a flat form
   - **Risk indicators / urgency** visually flagged (e.g. colored badge or highlighted line) so anything urgent is impossible to miss at a glance
   - Fields that are genuinely `null` are shown as "Not mentioned in note" rather than left blank or hidden, so the doctor knows the system checked and found nothing, rather than wondering if it was missed
3. **Grounding surfaced in the UI:** each displayed value is clickable/hoverable to reveal the exact source sentence from the note — bringing the "checkable, not just trusted" philosophy (Section 3) directly into what the doctor sees, not just into the underlying JSON.
4. **Not a replacement for the API:** the FastAPI endpoint (Phase 5) remains the actual programmatic interface; Streamlit is a thin, separate front-end that calls it and renders the result. This keeps the core deliverable (API + validated JSON) intact regardless of how the UI evolves.

---

## 6. Build Order

1. Read & understand the dataset (Phase 1) — pandas
2. Build the labeled eval set (Phase 2)
3. Prompt iteration V1 → V2 → V3 → Final, with grounded extraction (Phase 3) — Ollama
4. Runtime validation & repair loop (Phase 4) — Pydantic
5. API endpoint (Phase 5) — FastAPI
6. Evaluation — split metrics (Phase 6) — pandas/scikit-learn
7. ICD-10 bonus (Phase 7) — NLM API + local MCP server & client
8. Doctor-facing Streamlit view, styled like a prescription (Phase 8) — Streamlit, calling the Phase 5 API

---

## 7. What Makes This Approach Defensible

- Every design choice ties back to a real requirement in the brief: grounding → "no hallucination"; validation/repair → schema compliance; split evaluation → honest measurement given the dataset's actual content.
- The doctor-facing audience is stated explicitly, along with an honest account of where the dataset currently falls short of full clinical documentation (no medications/procedures) — framed as a scoping decision, not an oversight.
- The core pipeline is local, free, and requires no API key, which matters both technically (M1 Air resource limits) and narratively (privacy-first for a medical project). The ICD-10 bonus is the one deliberate exception — it calls a free public API — and that trade-off is stated explicitly rather than glossed over.
- The prescription-style Streamlit view directly serves the stated audience: doctors get a familiar, scannable document instead of a technical JSON blob, with grounding built into the presentation itself, not buried in the data.
- The core pipeline (Phases 1-6) is fully functional and gradeable without the UI or the bonus — both are additive, not dependencies.
