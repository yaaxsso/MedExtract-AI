"""
MedExtract AI — MCP client helper.

Spawns src/mcp_server.py as a subprocess over stdio and gives the reasoning
pipeline (src/api.py) a live, initialized MCP ClientSession. That session
gets passed straight into google-genai's `tools=[session]` config, which
drives automatic function calling over the MCP protocol — so every
diagnosis/medication/dosing/ICD-10 lookup the model makes is a real MCP
tool call, not a direct Python function reference.

Usage (from an async context):

    from src.mcp_client import mcp_session

    async with mcp_session() as session:
        response = await client.aio.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config={"tools": [session], ...},
        )

A new subprocess is spawned per call, kept alive only for that call, and
torn down when the `async with` block exits. For a single-user local/academic
deployment like this one, that's the simplest correct thing to do — no
shared server process to manage, no state to leak between notes. If this
were scaled up, the obvious next step would be a long-lived server process
reused across requests instead of spawning per-call.
"""
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Project root = the parent of the src/ directory this file lives in. Setting
# cwd explicitly means the client works the same whether it's launched from
# the repo root (e.g. `uvicorn src.api:app`) or from somewhere else.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

SERVER_PARAMS = StdioServerParameters(
    command=sys.executable,
    args=["-m", "src.mcp_server"],
    cwd=str(_PROJECT_ROOT),
)


@asynccontextmanager
async def mcp_session():
    """Async context manager yielding a live, initialized MCP ClientSession
    connected to the medical-tools server (src/mcp_server.py)."""
    async with stdio_client(SERVER_PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session
