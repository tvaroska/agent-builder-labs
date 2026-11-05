"""Control-plane provisioning script for the Tokenomics-governed Managed Agent.

Creates a reusable custom Agent resource on the Gemini Platform (`locations/global`)
extending a managed `base_agent` with:
- Remote MCP attachment to the Lab 2 `knowledge-catalog-mcp` Cloud Run server
  (for zero-scan semantic metric and schema discovery)
- Sandboxed `code_execution` where the agent writes and runs its own Python
  BigQuery REST tool (`/tmp/bq_runner.py`) with zero BigQuery MCP server,
  authenticated out-of-band by host egress proxy `transform` headers
- Portable `SKILL.md` mounting via Skill Registry, Cloud Storage, or inline file
"""

from __future__ import annotations

import os
import pathlib
import time
import uuid
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv

DEFAULT_BASE_AGENT = "antigravity-preview-05-2026"
SKILL_FILE = pathlib.Path(__file__).resolve().parent / "skills" / "tokenomics-analytics" / "SKILL.md"


def build_skill_source(
    *,
    skill_resource_name: str | None = None,
    skill_gcs_bucket: str | None = None,
    skill_path: pathlib.Path = SKILL_FILE,
) -> dict[str, str]:
    """Selects Skill Registry, Cloud Storage, or zero-setup inline SKILL.md mount."""
    if skill_resource_name:
        return {
            "type": "skill_registry",
            "source": skill_resource_name,
            "target": "./skills",
        }
    if skill_gcs_bucket:
        bucket_uri = (
            skill_gcs_bucket
            if skill_gcs_bucket.startswith("gs://")
            else f"gs://{skill_gcs_bucket}"
        )
        return {
            "type": "gcs",
            "source": bucket_uri,
            "target": "./skills",
        }
    return {
        "type": "inline",
        "target": ".agents/skills/tokenomics-analytics/SKILL.md",
        "content": skill_path.read_text(encoding="utf-8"),
    }


def build_egress_allowlist(catalog_mcp_url: str | None) -> list[dict[str, str]]:
    """Builds a least-privilege sandbox network allowlist for BigQuery REST + Catalog MCP."""
    domains = ["bigquery.googleapis.com"]
    if catalog_mcp_url:
        host = urlparse(catalog_mcp_url).hostname
        if host and host not in domains:
            domains.append(host)
    return [{"domain": d} for d in domains]


def build_agent_spec(
    *,
    agent_id: str,
    project_id: str,
    catalog_mcp_url: str,
    base_agent: str = DEFAULT_BASE_AGENT,
    skill_resource_name: str | None = None,
    skill_gcs_bucket: str | None = None,
) -> dict[str, Any]:
    """Constructs the declarative `client.agents.create` payload."""
    system_instruction = (
        "You are an enterprise Data Analytics Agent governed by strict Tokenomics rules. "
        "Read and follow the mounted tokenomics-analytics skill (.agents/skills or ./skills). "
        "Never run BigQuery jobs for metric formulas or schema questions—resolve those via "
        "knowledge-catalog-mcp. When live BigQuery rows are requested, build and execute "
        "/tmp/bq_runner.py inside code_execution against https://bigquery.googleapis.com "
        f"(billing project '{project_id}', dryRun<=100MB first, _TABLE_SUFFIX required, "
        "no SELECT *); outbound Authorization headers are injected automatically by the "
        "sandbox egress proxy."
    )

    tools: list[dict[str, Any]] = [
        {"type": "code_execution"},
        {
            "type": "mcp_server",
            "name": "knowledge-catalog-mcp",
            "url": catalog_mcp_url,
        },
    ]

    skill_source = build_skill_source(
        skill_resource_name=skill_resource_name,
        skill_gcs_bucket=skill_gcs_bucket,
    )

    return {
        "id": agent_id,
        "base_agent": base_agent,
        "description": "Tokenomics-governed analytics agent that builds and runs its own sandboxed BigQuery REST tool.",
        "system_instruction": system_instruction,
        "tools": tools,
        "base_environment": {
            "type": "remote",
            "env": {
                "GOOGLE_CLOUD_PROJECT": project_id,
            },
            "sources": [skill_source],
            "network": {
                "allowlist": build_egress_allowlist(catalog_mcp_url),
            },
        },
    }


def wait_for_agent_ready(client: Any, agent_id: str, timeout_sec: int = 45) -> Any:
    """Polls `client.agents.get` while asynchronous agent creation completes."""
    deadline = time.monotonic() + timeout_sec
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            details = client.agents.get(id=agent_id)
            if getattr(details, "id", None) == agent_id:
                return details
        except Exception as exc:
            last_exc = exc
        time.sleep(3)
    if last_exc is not None:
        raise RuntimeError(f"Timed out waiting for agent '{agent_id}': {last_exc}") from last_exc
    return client.agents.get(id=agent_id)


def main() -> None:
    load_dotenv()
    from google import genai

    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        raise ValueError("GOOGLE_CLOUD_PROJECT is required.")

    catalog_mcp_url = os.getenv("CATALOG_MCP_URL")
    if not catalog_mcp_url:
        raise ValueError("CATALOG_MCP_URL is required (e.g. https://knowledge-catalog-mcp-<hash>-uc.a.run.app/sse).")

    base_agent = os.getenv("MANAGED_BASE_AGENT", DEFAULT_BASE_AGENT)
    agent_id = os.getenv("MANAGED_AGENT_ID") or f"tokenomics-analytics-{uuid.uuid4().hex[:8]}"
    skill_resource_name = os.getenv("SKILL_RESOURCE_NAME") or None
    skill_gcs_bucket = os.getenv("AGENT_SKILL_GCS_BUCKET") or None

    client = genai.Client(enterprise=True, project=project_id, location="global")
    spec = build_agent_spec(
        agent_id=agent_id,
        project_id=project_id,
        catalog_mcp_url=catalog_mcp_url,
        base_agent=base_agent,
        skill_resource_name=skill_resource_name,
        skill_gcs_bucket=skill_gcs_bucket,
    )

    print(f"Creating Managed Agent '{agent_id}' (base_agent={base_agent})...")
    op = client.agents.create(**spec)
    print(f"Create operation submitted: {getattr(op, 'name', op)}")

    details = wait_for_agent_ready(client, agent_id)
    tools_summary = [getattr(t, "type", str(t)) for t in (getattr(details, "tools", None) or [])]

    print("\nManaged Agent Ready:")
    print(f"  ID:          {details.id}")
    print(f"  Base Agent:  {details.base_agent}")
    print(f"  Tools:       {tools_summary}")
    print(f"\nExport for test runs:\n  export MANAGED_AGENT_ID=\"{agent_id}\"")


if __name__ == "__main__":
    main()
