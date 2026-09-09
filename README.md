# MedExtract AI

A GenAI project that extracts structured clinical information from unstructured notes using a local LLM (Ollama), with voice input, read-back confirmation, grounded extraction, and an ICD-10 coding bonus.

See [docs/APPROACH.md](docs/APPROACH.md) for the full approach document — pipeline design, prompt iteration plan, and the reasoning behind each design decision.

## Status
🚧 In progress — approach finalized, implementation starting with Phase 1 (data review + failure taxonomy).

## Tech Stack
- **LLM:** Ollama (local), starting with `qwen2.5:7b-instruct`
- **Transcription:** Whisper (local)
- **Validation:** Pydantic
- **Dataset:** Kaggle "Patient Diaries and Clinical Notes Dataset"
