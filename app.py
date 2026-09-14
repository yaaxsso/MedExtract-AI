"""
MedExtract AI — Phase 8 Streamlit doctor-facing view.

Purely a presentation layer: this calls the Phase 5 FastAPI endpoint
(src/api.py) and renders the already-validated, MCP-verified JSON as a
printable, prescription/clinical-summary-style page. No extraction or
reasoning happens here.

Run:
    uvicorn src.api:app --reload      # in one terminal
    streamlit run app.py              # in another
"""
import os
import re
from datetime import datetime

import requests
import streamlit as st

API_URL = os.environ.get("MEDEXTRACT_API_URL", "http://127.0.0.1:8000")

st.set_page_config(page_title="MedExtract AI", page_icon="🩺", layout="centered")

URGENCY_STYLE = {
    "high": ("#B3261E", "#FDECEA"),
    "moderate": ("#946200", "#FFF4E0"),
    "low": ("#1E6B3E", "#E6F4EA"),
}

# ---------------------------------------------------------------------------
# Print styling: everything Streamlit renders normally, but @media print
# hides all the input chrome (sidebar, text areas, buttons) and only the
# .rx-pad container survives onto the printed page.
# ---------------------------------------------------------------------------
st.markdown("""
<style>
@media print {
    [data-testid="stSidebar"], [data-testid="stHeader"], .stButton,
    .stTextArea, .stTextInput, #print-btn-wrapper, .no-print { display: none !important; }
    .rx-pad { border: none !important; box-shadow: none !important; }
}
.rx-pad {
    font-family: 'Georgia', 'Times New Roman', serif;
    border: 2px solid #2b2b2b;
    border-radius: 4px;
    padding: 28px 36px;
    background: #fffdf8;
    color: #1a1a1a;
    margin-top: 10px;
}
.rx-header {
    display: flex; justify-content: space-between; align-items: flex-start;
    border-bottom: 2px solid #2b2b2b; padding-bottom: 10px; margin-bottom: 16px;
}
.rx-clinic { font-size: 1.4em; font-weight: 700; letter-spacing: 0.5px; }
.rx-subtitle { font-size: 0.85em; color: #555; font-style: italic; }
.rx-meta { text-align: right; font-size: 0.9em; }
.rx-section-label {
    font-size: 0.75em; text-transform: uppercase; letter-spacing: 1px;
    color: #6b6b6b; margin-top: 18px; margin-bottom: 4px; font-family: Arial, sans-serif;
}
.rx-symbol { font-size: 1.6em; font-weight: 700; margin-right: 8px; vertical-align: middle; }
.rx-med-box {
    border: 1px solid #b0a99a; border-left: 4px solid #2b2b2b;
    background: #fbf8f1; padding: 10px 14px; margin-bottom: 10px; border-radius: 2px;
}
.rx-med-name { font-size: 1.15em; font-weight: 700; }
.rx-med-dose { font-size: 0.95em; margin-top: 4px; line-height: 1.4; }
.rx-flag { font-size: 0.8em; color: #946200; font-style: italic; }
.rx-signature {
    margin-top: 34px; display: flex; justify-content: space-between;
    font-family: Arial, sans-serif; font-size: 0.9em;
}
.rx-sigline { border-top: 1px solid #333; width: 45%; padding-top: 4px; }
.rx-footnote {
    margin-top: 20px; font-size: 0.75em; color: #777; font-family: Arial, sans-serif;
    border-top: 1px dashed #ccc; padding-top: 8px;
}
</style>
""", unsafe_allow_html=True)


def not_mentioned(value):
    return value in (None, [], "")


def _clean(html: str) -> str:
    """Strip leading whitespace from every line. Markdown treats any line
    indented 4+ spaces as a code block, which silently breaks HTML rendering
    for multi-line f-strings that are indented for source readability."""
    return "\n".join(line.strip() for line in html.strip().splitlines())


def html_list_or_dash(value):
    if not_mentioned(value):
        return "<span style='color:#888;font-style:italic;'>Not mentioned in note</span>"
    if isinstance(value, list):
        return "; ".join(str(v) for v in value)
    return str(value)


def urgency_badge(urgency: str):
    color, bg = URGENCY_STYLE.get((urgency or "").lower(), ("#555", "#eee"))
    return (f'<span style="background-color:{bg};color:{color};'
            f'padding:4px 12px;border-radius:12px;font-weight:700;font-size:0.85em;'
            f'font-family:Arial, sans-serif;">{(urgency or "unknown").upper()}</span>')


def parse_dosing(dosing_text: str, meds: list):
    """Split the concatenated dosing_reference string into a leading note
    (if any) plus one clean block of text per medication, so each drug's
    dose can be shown in its own box instead of one long paragraph."""
    if not dosing_text or not meds:
        return None, {}

    note = ""
    remainder = dosing_text
    note_match = re.match(r"^(NOTE:.*?before use\.)\s*", dosing_text)
    if note_match:
        note = note_match.group(1)
        remainder = dosing_text[note_match.end():]

    # Find each "<DrugName>: " marker and slice the text between consecutive markers.
    positions = []
    for med in meds:
        m = re.search(rf"\b{re.escape(med)}\s*:\s*", remainder, flags=re.IGNORECASE)
        if m:
            positions.append((m.start(), m.end(), med))
    positions.sort(key=lambda p: p[0])

    per_drug = {}
    for i, (start, end, med) in enumerate(positions):
        stop = positions[i + 1][0] if i + 1 < len(positions) else len(remainder)
        per_drug[med] = remainder[end:stop].strip()

    return note, per_drug


st.title("🩺 MedExtract AI")
st.caption("Doctor-facing review — every diagnosis, medication, and dose below is a "
           "clinician-reviewed suggestion, not a finalized prescription.")

with st.sidebar:
    st.subheader("Connection")
    api_url = st.text_input("API base URL", value=API_URL)
    try:
        health = requests.get(f"{api_url}/health", timeout=3).json()
        st.success(f"Connected · model: {health.get('model')} · "
                   f"{health.get('api_keys_loaded')} key(s) loaded")
    except Exception:
        st.error("API not reachable — start it with:\n\nuvicorn src.api:app --reload")

note_text = st.text_area(
    "Patient note / diary entry",
    height=180,
    placeholder="Paste the clinical note or patient diary entry here...",
)
col_a, col_b = st.columns(2)
with col_a:
    note_ref = st.text_input("Patient / note reference", placeholder="e.g. Patient #042")
with col_b:
    clinic_name = st.text_input("Clinic / provider name (optional)", placeholder="e.g. Riverside Family Clinic")

analyze = st.button("Analyze note", type="primary", disabled=not note_text.strip())

if analyze:
    with st.spinner("Extracting, validating, and verifying against live medical sources..."):
        try:
            resp = requests.post(f"{api_url}/extract", json={"text": note_text}, timeout=120)
            resp.raise_for_status()
            result = resp.json()
        except Exception as e:
            st.error(f"Request failed: {e}")
            result = None

    if result:
        extraction = result.get("extraction", {}) or {}
        reasoning = result.get("diagnosis_reasoning", {}) or {}
        extraction_ok = result.get("extraction_valid")
        reasoning_ok = result.get("reasoning_valid")

        if not extraction_ok:
            st.warning("Extraction did not pass schema validation after repair attempts — "
                       "the summary below may be incomplete.")
        if not reasoning_ok:
            st.warning("Diagnosis reasoning failed — no diagnosis/medication section below.")

        st.markdown(
            '<div id="print-btn-wrapper">'
            '<button onclick="window.print()" '
            'style="padding:8px 18px;font-size:1em;border-radius:6px;border:1px solid #999;'
            'background:#f0f0f0;cursor:pointer;">🖨️ Print this page</button>'
            '</div>',
            unsafe_allow_html=True,
        )
        st.caption("If nothing happens, use your browser's print shortcut (Ctrl/Cmd+P) — "
                   "only the summary below will print either way.")

        diagnosis = reasoning.get("diagnosis")
        confidence = reasoning.get("confidence")
        icd10_candidates = reasoning.get("icd10_candidates") or []
        suggested_meds = reasoning.get("suggested_medications") or []
        unverified = reasoning.get("unverified_medications_mentioned")
        dosing_note, per_drug_dose = parse_dosing(reasoning.get("dosing_reference"), suggested_meds)

        icd10_html = ""
        if icd10_candidates:
            icd10_html = "<br>".join(
                f"<code>{c.get('code')}</code> — {c.get('description')}" for c in icd10_candidates
            )

        meds_html = ""
        if not_mentioned(suggested_meds):
            reason_bits = []
            if confidence:
                reason_bits.append(f"confidence: {confidence}")
            if reasoning.get("reasoning_notes"):
                reason_bits.append(reasoning["reasoning_notes"])
            reason_line = f" <span style='color:#aaa;'>({' — '.join(reason_bits)})</span>" if reason_bits else ""
            meds_html = (f"<div style='color:#888;font-style:italic;'>"
                         f"No medication suggested from this note.{reason_line}</div>")
        else:
            if dosing_note:
                meds_html += f"<div class='rx-flag'>⚠ {dosing_note}</div>"
            for med in suggested_meds:
                dose_text = per_drug_dose.get(med, "See full reference — dosing text not isolated.")
                meds_html += f"""
                <div class="rx-med-box">
                    <div class="rx-med-name">{med.capitalize()}</div>
                    <div class="rx-med-dose">{dose_text}</div>
                </div>
                """
            if reasoning.get("medication_selection_note"):
                meds_html += (f"<div class='rx-flag'>Note: "
                              f"{reasoning['medication_selection_note']}</div>")
        if unverified:
            meds_html += (f"<div class='rx-flag'>⚠ Mentioned but not verified as a real "
                          f"medication (excluded above): {', '.join(unverified)}</div>")

        rx_html = f"""
        <div class="rx-pad">
            <div class="rx-header">
                <div>
                    <div class="rx-clinic">{clinic_name or "MedExtract AI — Clinical Summary"}</div>
                    <div class="rx-subtitle">AI-assisted draft — for licensed clinician review &amp; co-signature</div>
                </div>
                <div class="rx-meta">
                    <div><b>Patient/Ref:</b> {note_ref or "—"}</div>
                    <div><b>Date:</b> {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
                    <div style="margin-top:6px;">{urgency_badge(extraction.get('urgency'))}</div>
                </div>
            </div>

            <div class="rx-section-label">Chief Complaint</div>
            <div>{html_list_or_dash(extraction.get('chief_complaint'))}</div>

            <div class="rx-section-label">Symptoms</div>
            <div>{html_list_or_dash(extraction.get('symptoms'))}</div>

            <div class="rx-section-label">Assessment / Diagnosis</div>
            <div><b>{diagnosis or 'No diagnosis suggested — insufficient evidence in note'}</b>
                {f" &nbsp; <span style='color:#666;font-size:0.85em;'>(confidence: {confidence})</span>" if diagnosis and confidence else ""}
            </div>
            {f"<div style='margin-top:4px;font-size:0.9em;'>{icd10_html}</div>" if icd10_html else ""}

            <div class="rx-section-label"><span class="rx-symbol">℞</span>Medications — Suggested, For Clinician Review</div>
            {meds_html}

            <div class="rx-section-label">Medical History</div>
            <div>{html_list_or_dash(extraction.get('medical_history'))}</div>

            <div class="rx-section-label">Procedures</div>
            <div>{html_list_or_dash(extraction.get('procedures'))}</div>

            <div class="rx-section-label">Follow-up</div>
            <div>{html_list_or_dash(extraction.get('follow_up'))}</div>

            <div class="rx-section-label">Risk Indicators</div>
            <div>{html_list_or_dash(extraction.get('risk_indicators'))}</div>

            <div class="rx-section-label">Summary</div>
            <div>{extraction.get('summary') or '—'}</div>

            <div class="rx-signature">
                <div class="rx-sigline">Clinician signature</div>
                <div class="rx-sigline">Date</div>
            </div>
            <div class="rx-footnote">
                Generated by MedExtract AI. Diagnosis, medications, and dosing are
                MCP-verified against live medical data sources (MedlinePlus, RxNorm,
                DailyMed, NLM Clinical Tables) but are draft suggestions only — not a
                valid prescription until reviewed and signed by a licensed clinician.
                Dosing shown is standard reference information, not patient-specific.
            </div>
        </div>
        """
        st.markdown(_clean(rx_html), unsafe_allow_html=True)

        with st.expander("Raw response (debug)"):
            st.json(result)