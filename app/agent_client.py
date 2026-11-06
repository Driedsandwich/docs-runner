"""Call a Docs Runner deployment from an AI agent or a script.

`audit()` submits a public quickstart URL, waits for the job to finish and returns a
compact, machine-readable verdict. The MCP server in `integrations/mcp/server.py` and
the command line (`python -m app.agent_client <url>`) both use it.
"""
import argparse
import asyncio
import json
import os
import sys
import time

import httpx

DEFAULT_BASE = "https://64-177-58-251.sslip.io"
TERMINAL = {"completed", "failed", "timed_out", "cancelled"}


def base_url() -> str:
    return os.environ.get("DOCSRUNNER_URL", DEFAULT_BASE).rstrip("/")


def summarize(job: dict, base: str) -> dict:
    """Keep what an agent needs to decide whether to follow the document."""
    steps = []
    for item in job.get("steps") or []:
        attempts = item.get("attempts") or []
        first = attempts[0]["evidence"] if attempts else {}
        steps.append({
            "id": (item.get("step") or {}).get("id"),
            "command": (item.get("step") or {}).get("command"),
            "status": item.get("status"),
            "reason": item.get("reason"),
            "exit_code": first.get("exit_code"),
            "stderr_tail": (first.get("stderr") or "")[-300:],
            "suggested_fix": (item.get("diagnosis") or {}).get("suggested_command"),
            "fix_result": item.get("retry_status"),
        })
    statuses = [s["status"] for s in steps]
    presentation = job.get("presentation") or {}
    cleanup = job.get("cleanup") or {}
    return {
        "job_id": job.get("id"),
        "state": job.get("state"),
        "error": job.get("error"),
        "runtime": job.get("runtime"),
        "counts": {name: statuses.count(name) for name in sorted(set(statuses))},
        "injection_detected": bool((job.get("injection") or {}).get("injection_detected")),
        "containment_events": [e.get("kind") for e in presentation.get("containment_events", [])],
        "denied_destinations": cleanup.get("denied_destinations", []),
        "sandbox_removed": cleanup.get("sandbox_removed"),
        "steps": steps,
        "report_url": f"{base}/api/jobs/{job.get('id')}",
    }


async def audit(url: str, *, base: str | None = None, client: httpx.AsyncClient | None = None,
                timeout: float = 330, poll: float = 3) -> dict:
    """Run one audit. Returns a summary, or {"error": ..., "http_status": ...} if refused."""
    base = (base or base_url()).rstrip("/")
    own = client is None
    client = client or httpx.AsyncClient(timeout=30, follow_redirects=False)
    try:
        response = await client.post(f"{base}/api/jobs", json={"url": url})
        if response.status_code != 202:
            detail = response.json().get("detail") if "json" in response.headers.get("content-type", "") else response.text[:300]
            return {"error": detail, "http_status": response.status_code,
                    "retry_after_seconds": response.headers.get("retry-after")}
        job_id = response.json()["id"]
        deadline = time.monotonic() + timeout
        while True:
            job = (await client.get(f"{base}/api/jobs/{job_id}")).json()
            if job.get("state") in TERMINAL and job.get("phase") == "finished" or job.get("state") in {"failed", "cancelled", "timed_out"}:
                return summarize(job, base)
            if time.monotonic() > deadline:
                return {"error": "Timed out waiting for the job", "job_id": job_id,
                        "report_url": f"{base}/api/jobs/{job_id}"}
            await asyncio.sleep(poll)
    finally:
        if own:
            await client.aclose()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Audit a public quickstart with Docs Runner and print JSON.")
    parser.add_argument("url", help="public README or quickstart URL (http/https, ports 80/443)")
    parser.add_argument("--base", default=None, help=f"Docs Runner deployment (default $DOCSRUNNER_URL or {DEFAULT_BASE})")
    args = parser.parse_args(argv)
    result = asyncio.run(audit(args.url, base=args.base))
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if result.get("state") == "completed" and not result.get("error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
