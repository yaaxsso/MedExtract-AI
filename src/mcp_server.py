"""
MedExtract AI — Phase 3.5 MCP server.

Exposes the four live medical-verification/lookup tools as real MCP tools,
over stdio: condition_info_lookup, medication_lookup, dosing_lookup,
icd10_lookup. The implementations themselves live in src/medical_tools.py —
this file's only job is to register them with FastMCP and run the transport.

This process is not started manually in normal operation. src/mcp_client.py
spawns it as a subprocess (`python -m src.mcp_server`) each time the
reasoning pipeline needs a verified diagnosis/medication/dosing/ICD-10
answer, and talks to it over stdin/stdout using the MCP protocol.

Manual smoke test (run from the project root):
    python -m src.mcp_server
Then, from another process, use mcp_client.mcp_session() to connect, or use
the official `mcp dev` / `mcp inspector` CLI tools against this file.
"""
from mcp.server.fastmcp import FastMCP

from src.medical_tools import (
    condition_info_lookup as _condition_info_lookup,
    medication_lookup as _medication_lookup,
    dosing_lookup as _dosing_lookup,
    icd10_lookup as _icd10_lookup,
)

mcp = FastMCP("medextract-medical-tools")


@mcp.tool()
def condition_info_lookup(condition_text: str, max_results: int = 1) -> dict:
    """Look up authoritative medical info about a condition from MedlinePlus. Use this to verify a diagnosis before stating it."""
    return _condition_info_lookup(condition_text, max_results)


@mcp.tool()
def medication_lookup(drug_name: str) -> dict:
    """Verify a medication name is real before suggesting it. If found is False, do not suggest that medication."""
    return _medication_lookup(drug_name)


@mcp.tool()
def dosing_lookup(drug_name: str) -> dict:
    """Get standard FDA label dosing text for an already-verified medication. Generic reference only, never a personalized dose."""
    return _dosing_lookup(drug_name)


@mcp.tool()
def icd10_lookup(diagnosis_text: str, max_results: int = 3) -> dict:
    """Look up official ICD-10-CM candidate codes for a confirmed diagnosis. Returns up to
    max_results candidates — keep all of them, do not narrow to one yourself. Never state a
    code from memory."""
    return _icd10_lookup(diagnosis_text, max_results)


if __name__ == "__main__":
    mcp.run(transport="stdio")
