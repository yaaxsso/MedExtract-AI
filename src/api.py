"""
MedExtract AI — Phase 5 API endpoint (initial version).
Wraps the extraction + diagnosis-reasoning pipeline behind a single FastAPI route.
Known issues from this version are documented in data/phase6_findings.md — this
implementation is being carried forward into a fresh branch for improvement.
"""
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="MedExtract AI")


class NoteInput(BaseModel):
    text: str


@app.post("/extract")
def extract_note(note: NoteInput):
    """
    Full pipeline: extraction (Phase 3) -> diagnosis/medication reasoning (Phase 3.5).
    NOTE: known issues at time of writing — see data/phase6_findings.md:
    - occasional false-positive diagnosis on clearly positive/neutral text
    - confidence field not always discriminating between strong/weak evidence
    """
    # extraction_result = extract_final(note.text)
    # reasoning_result = reason_diagnosis_v2(note.text)
    # return {"extraction": extraction_result, "reasoning": reasoning_result}
    raise NotImplementedError("Wire up extract_final and reason_diagnosis_v2 from the notebook here.")