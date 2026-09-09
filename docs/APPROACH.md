# MedExtract AI — Approach Document

## 1. Project Summary

MedExtract AI takes an unstructured clinical note and uses a local LLM (via Ollama) to extract only the information explicitly stated in the note into a fixed JSON schema (chief complaint, symptoms, diagnosis, medical history, medications, procedures, follow-up, summary, risk indicators, urgency). The system must never invent, diagnose, or prescribe — extraction only. Output is validated against a schema, served through a small API, and evaluated on a labeled test set rather than judged by eye.

**Design philosophy: verify-before-you-trust.** The real risk with AI extraction in healthcare isn't just accuracy — it's that confidently-wrong structured data looks identical to correct data once it's saved. So instead of treating extraction as a one-shot "trust the model" step, every output in this system is either checkable against its source, honestly labeled as missing rather than guessed, or confirmed back to the speaker before being finalized. Three features carry this philosophy:

- **Grounded extraction** — every extracted field is tagged with the sentence in the note that supports it, so a claim can be checked against the source instantly rather than by re-reading the whole note.
- **Voice input with read-back confirmation** — when input is spoken, the system reads back what it understood *before* anything is finalized, so a transcription or extraction error can be caught and corrected in the moment — not after a wrong value is already sitting in a structured record.
- **ICD-10 coding as a bonus tool-calling step** — extends the verified diagnosis into a standard clinical code, again through a checkable lookup rather than the model guessing a code from memory.

The core text pipeline (Phases 1–5) is the non-negotiable graded deliverable. Voice, read-back, and grounding are additions layered on top, designed so the core still works completely on its own if any addition runs short on time.

---

## 2. Tech Choices

- **LLM:** Local via **Ollama**. Start with `qwen2.5:7b-instruct` (strong instruction-following for its size, good default for an M1 Air). Only move to a larger model (e.g. `qwen2.5:14b-instruct`, RAM-permitting) if V2/V3 prompt fixes can't solve a persistent failure category — and if that upgrade happens, document it as a finding, not just a fix.
- **Transcription:** Local Whisper (`faster-whisper` or `whisper.cpp`) for the voice input path.
- **Read-back / text-to-speech:** macOS's built-in `say` command — zero setup, already on the machine.
- **Validation:** Pydantic models mirroring the JSON schema exactly.
- **Dataset:** Kaggle "Patient Diaries and Clinical Notes Dataset" (synthetic/public — no real PHI).

---

## 3. Pipeline Phases

### Phase 1 — Data & Failure Taxonomy
Read ~20–30 notes manually before writing any prompts. Build a checklist of things that could trip up extraction:
- Negation ("denies fever", "no chest pain")
- Implied vs. explicitly stated info
- Multiple medications in one sentence
- Ambiguous or missing dosages
- Notes with no diagnosis at all
- Family history vs. the patient's own history

This taxonomy becomes both the design basis for the test set (Phase 2) and the rubric for judging each prompt version (Phase 3).

### Phase 2 — Labeled Evaluation Set
Hand-label ~15–20 notes with the expected "gold" JSON output. This is what makes "V2 is better than V1" a measurable, provable claim instead of an impression.

### Phase 3 — Prompt Iteration Loop
- **V1 (baseline):** schema + instructions, no examples. Run on the eval set, log every failure by category from the Phase 1 taxonomy.
- **V2:** patch the worst offenders — explicit, repeated rules for "do not invent," strict null-handling.
- **V3:** add few-shot examples targeting the hardest categories (negation, ambiguity); tighten format constraints.
- **Final:** consolidate changes; write up *why* each change helped, backed by eval-set evidence — this write-up is the graded core of the assignment.

**Grounded extraction:** each field in the schema also carries a `source_sentence` (or `null` if not present in the note). Reinforces the "no hallucination" requirement with something checkable, and doubles as a natural signal for low-confidence fields.

**Completeness labeling:** the returned JSON stays exactly as specced — a field genuinely absent from the note is still just `null`, no schema changes. The distinction between "nothing to extract" and "extraction failed" is tracked separately, outside the API response, in the extraction logs and evaluation harness (e.g. "X% of nulls were confirmed-absent-from-note vs. Y% were validation failures"). This keeps the output format fully compliant while still surfacing the insight in the write-up.

### Phase 4 — Schema Enforcement & Validation
- Use Ollama's `format: json` mode as a baseline guardrail (guarantees parseable JSON, not schema-correctness).
- Pydantic validates against the full schema.
- On validation failure: re-prompt the LLM with the specific validation error and retry (1–2 attempts) before logging a hard failure.
- Because local models are less consistently obedient than large hosted models, this retry/repair loop is load-bearing here, not just a nice-to-have.

### Phase 5 — API & Evaluation Harness
- Minimal API exposing a single extraction endpoint (note in → validated JSON out).
- Evaluation harness runs the final prompt across the full labeled set and reports a concrete metric (e.g. field-level accuracy or exact-match rate) — proof of performance, not visual spot-checks.

### Phase 6 — Voice Input with Read-Back Confirmation (Differentiator)
This is the centerpiece "different approach" for the project — a genuine trust mechanism, not just an alternate input format.

1. **Capture:** a doctor dictates a note as audio (recorded or a synthetic reading of an existing note for demo purposes).
2. **Transcribe:** local Whisper converts audio → raw text transcript.
3. **Read-back before anything is saved:** before the transcript is sent into extraction, the system speaks it back (macOS `say`) — "I heard: patient reports chest pain, denies shortness of breath." This catches transcription errors *before* they can propagate into a wrong diagnosis sitting silently in a structured record.
4. **Confirm or correct:** in a live demo, the "doctor" can confirm or redo the dictation at this point. (For the written project, this can be shown as a manual confirmation step in the pipeline flow — full voice-based correction is a stretch goal, not required.)
5. **Extract:** the confirmed transcript flows into the exact same Phase 3 extraction pipeline — no separate downstream logic needed.

Framing for the write-up: *a verify-before-you-trust pipeline — audio in, read back for confirmation, structured JSON out, nothing touches the cloud at any step.*

---

## 4. Bonus — ICD-10 Coding via Tool-Calling Agent

**Goal:** extend each verified diagnosis into a standard ICD-10 code, using a lookup tool rather than letting the model guess a code from memory (models are unreliable at recalling exact medical codes, so this must be a real lookup, not a hallucinated one).

**How it will work:**

1. **Build the lookup tool.** Download a public ICD-10-CM code table (CSV of code → description). Write a local Python function `icd10_lookup(diagnosis_text: str) -> list[{code, description, score}]` that matches the diagnosis text against the table using fuzzy string matching (e.g. `rapidfuzz`) or embedding similarity, returning the top 1–3 candidate codes.
2. **Expose it as a tool.** Define `icd10_lookup` as a tool schema (name, description, parameters) in Ollama's tool-calling format. Ollama supports function/tool calling for compatible models (e.g. `qwen2.5`, `llama3.1`).
3. **Agent step, after extraction is validated.** Once the core JSON (with a confirmed `diagnosis` field) passes validation, run a second short agent turn: give the model the diagnosis text and the `icd10_lookup` tool, and instruct it to call the tool to find the matching code rather than answer from memory. The model issues a tool call, the local function executes, and the result is returned to the model to produce the final `icd10_codes` field (code + description, tied back to the diagnosis).
4. **Fallback plan.** If a given local model doesn't support tool calling reliably, fall back to calling `icd10_lookup` directly in code right after extraction (deterministic, no agent turn) — still satisfies the requirement, just without the "model decides to call a tool" agentic framing. This is documented as a graceful degradation, not a failure.
5. **Validate output.** The final `icd10_codes` field only ever contains codes that came from the lookup table — never a code typed by the model directly — keeping the same "no hallucination" guarantee as the rest of the pipeline.

---

## 5. Build Order (Solo, Realistic Sequencing)

1. Manual note review + failure taxonomy
2. Label the small eval set
3. V1 prompt → run → log failures
4. V2 → V3 → Final prompt, each measured against the eval set
5. Add `source_sentence` grounding + completeness labeling to the schema and prompt
6. Pydantic validation + retry/repair loop
7. API endpoint
8. Evaluation harness across the full labeled set
9. Voice input + read-back confirmation layer (Whisper + `say` → same pipeline)
10. ICD-10 tool-calling bonus, if time allows

---

## 6. What Makes This Approach Defensible

- Every design choice ties back to a real requirement in the brief (grounding → "no hallucination"; retry/repair → schema compliance; eval harness → "not just visual inspection"; voice → the instructor's own example).
- The core deliverable is fully functional without the voice/read-back layer — the differentiator is additive risk, not a dependency.
- The read-back step addresses the actual failure mode structured-extraction systems are dangerous for: a wrong value that looks exactly like a right one once it's in JSON.
- Local-only architecture (Ollama + local Whisper + local ICD-10 lookup) is a coherent, easy-to-explain story for a *medical* project specifically: no patient data ever leaves the machine, even during transcription or coding.
