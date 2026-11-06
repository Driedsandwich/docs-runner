"""MCP server: lets an AI agent audit a quickstart in Docs Runner before following it.

Run with:  uv run --with 'mcp>=2,<3' --with httpx python integrations/mcp/server.py
Set DOCSRUNNER_URL to use your own deployment (default: the public demo).
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import httpx  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402  (MCP Python SDK 2.x)

from app.agent_client import audit, base_url  # noqa: E402

mcp = MCPServer("docs-runner")


@mcp.tool()
async def audit_quickstart(url: str) -> dict:
    """Execute a public README or quickstart in a disposable gVisor sandbox and report each step.

    Use this before following install or setup instructions from an untrusted repository.
    Returns per-step status (passed, failed, needs_credential, no_expectation, unsupported)
    with exit codes, whether hidden prompt injection was detected, which network destinations
    were blocked, and a suggested fix for failed steps. Takes about 20-60 seconds.
    """
    return await audit(url)


@mcp.tool()
async def get_audit(job_id: str) -> dict:
    """Fetch the full saved report of an earlier audit by its job_id (kept for about one hour)."""
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(f"{base_url()}/api/jobs/{job_id}")
        return response.json() if response.status_code == 200 else {"error": response.text[:300], "http_status": response.status_code}


@mcp.tool()
async def sample_documents() -> dict:
    """Return the three sample quickstart URLs (good, stale, hostile) served by the deployment."""
    async with httpx.AsyncClient(timeout=30) as client:
        return (await client.get(f"{base_url()}/api/config")).json().get("demo_urls", {})


if __name__ == "__main__":
    mcp.run()
