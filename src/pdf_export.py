"""
MedExtract AI — PDF export.

Builds an actual downloadable PDF file of the doctor-facing prescription /
clinical-summary card (the same content as the .rx-pad on screen), so the
user can click "Download PDF" and get a single-page file on disk instead of
using the browser's Print dialog.

Pure-Python (reportlab) — no system-level dependencies required.
"""
from __future__ import annotations

from datetime import datetime
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    HRFlowable,
)

URGENCY_COLORS = {
    "high": colors.HexColor("#B3261E"),
    "moderate": colors.HexColor("#946200"),
    "low": colors.HexColor("#1E6B3E"),
}

INK = colors.HexColor("#1a1a1a")
MUTED = colors.HexColor("#6b6b6b")
FLAG = colors.HexColor("#946200")
BORDER = colors.HexColor("#2b2b2b")
MED_BORDER = colors.HexColor("#b0a99a")
MED_BG = colors.HexColor("#fbf8f1")


def _not_mentioned(value):
    return value in (None, [], "")


def _text_or_dash(value):
    if _not_mentioned(value):
        return "<i><font color='#888888'>Not mentioned in note</font></i>"
    if isinstance(value, list):
        return "; ".join(str(v) for v in value)
    return str(value)


def _escape(text):
    if text is None:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def generate_prescription_pdf(
    extraction: dict,
    reasoning: dict,
    per_drug_dose: dict,
    dosing_note: str,
    note_ref: str = "",
    clinic_name: str = "",
) -> bytes:
    """Render the clinical summary / draft prescription as a one-page PDF
    and return the raw PDF bytes, ready for a Streamlit download button."""

    extraction = extraction or {}
    reasoning = reasoning or {}

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=LETTER,
        topMargin=18 * mm,
        bottomMargin=16 * mm,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        title="MedExtract AI - Clinical Summary",
    )

    styles = {
        "clinic": ParagraphStyle("clinic", fontName="Times-Bold", fontSize=15, textColor=INK, leading=18),
        "subtitle": ParagraphStyle("subtitle", fontName="Times-Italic", fontSize=9, textColor=MUTED, leading=12),
        "meta": ParagraphStyle("meta", fontName="Helvetica", fontSize=9, textColor=INK, alignment=2, leading=13),
        "label": ParagraphStyle(
            "label", fontName="Helvetica-Bold", fontSize=8, textColor=MUTED,
            spaceBefore=10, spaceAfter=3, leading=10,
        ),
        "body": ParagraphStyle("body", fontName="Times-Roman", fontSize=10.5, textColor=INK, leading=14),
        "medname": ParagraphStyle("medname", fontName="Times-Bold", fontSize=11.5, textColor=INK, leading=14),
        "meddose": ParagraphStyle("meddose", fontName="Times-Roman", fontSize=10, textColor=INK, leading=13),
        "flag": ParagraphStyle("flag", fontName="Helvetica-Oblique", fontSize=8.5, textColor=FLAG, leading=11),
        "footnote": ParagraphStyle("footnote", fontName="Helvetica", fontSize=7.5, textColor=MUTED, leading=10),
        "sig": ParagraphStyle("sig", fontName="Helvetica", fontSize=9, textColor=INK, leading=12),
    }

    flow = []

    # --- Header -----------------------------------------------------------
    urgency = (extraction.get("urgency") or "unknown").lower()
    urgency_color = URGENCY_COLORS.get(urgency, colors.HexColor("#555555"))
    urgency_para = Paragraph(
        f"<font color='#{urgency_color.hexval()[-6:]}'><b>{_escape(urgency.upper())}</b></font>",
        styles["meta"],
    )

    left_col = [
        Paragraph(_escape(clinic_name) or "MedExtract AI &mdash; Clinical Summary", styles["clinic"]),
        Paragraph("AI-assisted draft &mdash; for licensed clinician review &amp; co-signature", styles["subtitle"]),
    ]
    right_col = [
        Paragraph(f"<b>Patient/Ref:</b> {_escape(note_ref) or '&mdash;'}", styles["meta"]),
        Paragraph(f"<b>Date:</b> {datetime.now().strftime('%Y-%m-%d %H:%M')}", styles["meta"]),
        Spacer(1, 2),
        urgency_para,
    ]

    header_table = Table(
        [[left_col, right_col]],
        colWidths=[100 * mm, 60 * mm],
    )
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    flow.append(header_table)
    flow.append(Spacer(1, 6))
    flow.append(HRFlowable(width="100%", thickness=1.3, color=BORDER))
    flow.append(Spacer(1, 4))

    def section(label, body_flowable):
        flow.append(Paragraph(label.upper(), styles["label"]))
        flow.append(body_flowable)

    section("Chief Complaint", Paragraph(_text_or_dash(extraction.get("chief_complaint")), styles["body"]))
    section("Symptoms", Paragraph(_text_or_dash(extraction.get("symptoms")), styles["body"]))

    # --- Assessment / diagnosis --------------------------------------------
    diagnosis = reasoning.get("diagnosis")
    confidence = reasoning.get("confidence")
    icd10_candidates = reasoning.get("icd10_candidates") or []

    diag_text = f"<b>{_escape(diagnosis) if diagnosis else 'No diagnosis suggested &mdash; insufficient evidence in note'}</b>"
    if diagnosis and confidence:
        diag_text += f" &nbsp; <font color='#666666' size='8.5'>(confidence: {_escape(confidence)})</font>"
    diag_flow = [Paragraph(diag_text, styles["body"])]
    if icd10_candidates:
        icd_lines = "<br/>".join(
            f"<font face='Courier'>{_escape(c.get('code'))}</font> &mdash; {_escape(c.get('description'))}"
            for c in icd10_candidates
        )
        diag_flow.append(Spacer(1, 2))
        diag_flow.append(Paragraph(icd_lines, ParagraphStyle("icd", parent=styles["body"], fontSize=9.5)))
    section("Assessment / Diagnosis", Table([[f] for f in diag_flow], style=TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ])))

    # --- Medications --------------------------------------------------------
    suggested_meds = reasoning.get("suggested_medications") or []
    unverified = reasoning.get("unverified_medications_mentioned")

    med_flow = []
    if _not_mentioned(suggested_meds):
        reason_bits = []
        if confidence:
            reason_bits.append(f"confidence: {confidence}")
        if reasoning.get("reasoning_notes"):
            reason_bits.append(reasoning["reasoning_notes"])
        reason_line = f" <font color='#aaaaaa'>({_escape(' &mdash; '.join(reason_bits))})</font>" if reason_bits else ""
        med_flow.append(Paragraph(
            f"<i><font color='#888888'>No medication suggested from this note.</font></i>{reason_line}",
            styles["body"],
        ))
    else:
        if dosing_note:
            med_flow.append(Paragraph(f"[!] {_escape(dosing_note)}", styles["flag"]))
            med_flow.append(Spacer(1, 3))
        for med in suggested_meds:
            dose_text = per_drug_dose.get(med, "See full reference — dosing text not isolated.")
            box = Table(
                [[Paragraph(_escape(med).capitalize(), styles["medname"])],
                 [Paragraph(_escape(dose_text), styles["meddose"])]],
                colWidths=[160 * mm],
            )
            box.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), MED_BG),
                ("BOX", (0, 0), (-1, -1), 0.6, MED_BORDER),
                ("LINEBEFORE", (0, 0), (0, -1), 2.5, BORDER),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            med_flow.append(box)
            med_flow.append(Spacer(1, 4))
        if reasoning.get("medication_selection_note"):
            med_flow.append(Paragraph(f"Note: {_escape(reasoning['medication_selection_note'])}", styles["flag"]))
    if unverified:
        med_flow.append(Paragraph(
            f"[!] Mentioned but not verified as a real medication (excluded above): "
            f"{_escape(', '.join(unverified))}",
            styles["flag"],
        ))

    flow.append(Paragraph("Rx &nbsp;MEDICATIONS — SUGGESTED, FOR CLINICIAN REVIEW", styles["label"]))
    flow.extend(med_flow)

    section("Medical History", Paragraph(_text_or_dash(extraction.get("medical_history")), styles["body"]))
    section("Procedures", Paragraph(_text_or_dash(extraction.get("procedures")), styles["body"]))
    section("Follow-up", Paragraph(_text_or_dash(extraction.get("follow_up")), styles["body"]))
    section("Risk Indicators", Paragraph(_text_or_dash(extraction.get("risk_indicators")), styles["body"]))
    section("Summary", Paragraph(_escape(extraction.get("summary")) or "&mdash;", styles["body"]))

    # --- Signature ----------------------------------------------------------
    flow.append(Spacer(1, 22))
    sig_table = Table(
        [[HRFlowable(width="100%", thickness=0.7, color=colors.HexColor("#333333")),
          HRFlowable(width="100%", thickness=0.7, color=colors.HexColor("#333333"))]],
        colWidths=[75 * mm, 75 * mm],
    )
    flow.append(sig_table)
    label_table = Table(
        [[Paragraph("Clinician signature", styles["sig"]), Paragraph("Date", styles["sig"])]],
        colWidths=[75 * mm, 75 * mm],
    )
    flow.append(label_table)

    # --- Footnote -------------------------------------------------------------
    flow.append(Spacer(1, 12))
    flow.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc"), dash=(2, 2)))
    flow.append(Spacer(1, 4))
    flow.append(Paragraph(
        "Generated by MedExtract AI. Diagnosis, medications, and dosing are MCP-verified against live "
        "medical data sources (MedlinePlus, RxNorm, DailyMed, NLM Clinical Tables) but are draft "
        "suggestions only &mdash; not a valid prescription until reviewed and signed by a licensed "
        "clinician. Dosing shown is standard reference information, not patient-specific.",
        styles["footnote"],
    ))

    doc.build(flow)
    return buf.getvalue()