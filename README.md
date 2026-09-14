# MedExtract AI

A GenAI project that extracts structured clinical information from unstructured
patient notes and diaries, reasons toward a diagnosis and medication suggestions,
and verifies every medical claim live against real external data sources (never
the model's own memory) before showing it to a doctor.

See [docs/APPROACH.md](docs/APPROACH.md) for the full approach document — pipeline
design, prompt iteration history, and the reasoning behind each decision.

## Status
Phases 1–6 are implemented and gradeable end to end: dataset exploration, a
hand-labeled gold eval set, prompt iteration (V1 → Final), Pydantic
validation + repair, live MCP-verified diagnosis/medication/dosing/ICD-10
reasoning, a FastAPI endpoint, and evaluation metrics. Phase 8 (Streamlit
doctor-facing view) is included as an additive front end over the API.

## Tech Stack
- **LLM:** Google Gemini (`gemini-3.5-flash-lite`), via `google-genai`, with multi-key fallback on rate limits
- **Validation:** Pydantic (schema validation + automatic repair loop)
- **Medical verification:** live MCP tool calls — MedlinePlus Connect (condition info), RxNorm (medication verification), DailyMed/openFDA (dosing), NLM Clinical Tables (ICD-10 codes)
- **Tool-calling protocol:** MCP (`mcp` Python SDK) — `src/mcp_server.py` exposes the four lookups as real MCP tools over stdio; `src/mcp_client.py` spawns and connects to it. Falls back to direct in-process calls if the MCP subprocess can't be reached.
- **API:** FastAPI (`src/api.py`) — `POST /extract` runs the full pipeline (extraction → validation/repair → diagnosis/medication/dosing/ICD-10 reasoning) for a note
- **Doctor-facing UI:** Streamlit (`app.py`) — renders the API's output as a prescription/report-style view
- **Dataset:** Kaggle "Patient Diaries and Clinical Notes Dataset" (`clinical_notes.csv`, `patient_diaries.csv`)

## Repo Layout
```
notebooks/project_final.ipynb   Full pipeline walkthrough — dataset exploration through evaluation
src/api.py                      FastAPI app: the production pipeline
src/mcp_server.py               MCP server exposing the 4 medical-lookup tools
src/mcp_client.py               Spawns/connects to the MCP server for src/api.py
src/medical_tools.py            The 4 lookup implementations (shared by server + fallback path)
app.py                          Streamlit doctor-facing view, calls the FastAPI endpoint
data/raw/                       Kaggle source CSVs (gitignored — not redistributed)
data/labeled/                   Hand-labeled gold-standard data
data/eval_sets/                 Gold evaluation sets (JSONL)
data/results/                   Saved batch-run outputs used for evaluation
docs/APPROACH.md                Full approach document
```

## Setup
1. `pip install -r requirements.txt`
2. Create a `.env` file in the project root with at least one Gemini key:
   ```
   GEMINI_API_KEY_1=your_key_here
   # optional additional keys for rate-limit fallback:
   # GEMINI_API_KEY_2=...
   # GEMINI_API_KEY_3=...
   ```
   (A single `GEMINI_API_KEY` also works if you only have one key.)
3. Place `clinical_notes.csv` and `patient_diaries.csv` in `data/raw/` (not committed — see Dataset note below).

## Running it

**Notebook** (full walkthrough — dataset, prompt iteration, validation, MCP verification, evaluation):
```
jupyter notebook notebooks/project_final.ipynb
```
Run top to bottom.

**API:**
```
uvicorn src.api:app --reload
```
Then `POST http://127.0.0.1:8000/extract` with `{"text": "..."}`.

**Streamlit UI** (requires the API running first):
```
streamlit run app.py
```

## Dataset note
`data/raw/` is gitignored — the Kaggle dataset isn't redistributed in this repo
per its license. Everything derived from it that's part of the graded work
(hand-labeled gold data, eval sets, batch-run results) **is** committed under
`data/labeled/`, `data/eval_sets/`, and `data/results/`.