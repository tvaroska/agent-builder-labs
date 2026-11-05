"""Step 1: Create a reusable Managed Agent (`client.agents.create`).

Works out-of-the-box in a fresh/empty Qwiklabs Google Cloud project:
- Mounts the local `./skills/tokenomics-analytics/SKILL.md` bundled in this
  directory (`inline` by default, or from GCS if staged by `./instructions.sh`).
- Enables sandboxed `code_execution` and restricts outbound egress to
  `bigquery.googleapis.com` (plus an optional `CATALOG_MCP_URL` if set).
- Persists `agent_id` to `.lab_state.json` for Steps 2, 3, and 4.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import time
import uuid
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv
from low_infra_harness import LAB_DIR, resolve_project_id, save_lab_state

DEFAULT_BASE_AGENT = "antigravity-preview-05-2026"
SKILL_FILE = LAB_DIR / "skills" / "tokenomics-analytics" / "SKILL.md"


def build_skill_source(
    *,
    skill_gcs_bucket: str | None = None,
    skill_resource_name: str | None = None,
    skill_path: pathlib.Path = SKILL_FILE,
) -> dict[str, str]:
    """Mounts local `SKILL.md` inline by default (or from GCS / Skill Registry if set)."""
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


def build_egress_allowlist(catalog_mcp_url: str | None = None) -> list[dict[str, str]]:
    """Builds a least-privilege sandbox network allowlist for BigQuery REST (+ optional MCP)."""
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
    catalog_mcp_url: str | None = None,
    base_agent: str = DEFAULT_BASE_AGENT,
    model: str | None = None,
    max_total_tokens: int | None = None,
    skill_gcs_bucket: str | None = None,
    skill_resource_name: str | None = None,
) -> dict[str, Any]:
    """Constructs the declarative `client.agents.create` payload."""
    system_instruction = (
        "You are an enterprise Data Analytics Agent governed by strict Tokenomics rules. "
        "Read and follow the mounted tokenomics-analytics skill (.agents/skills or ./skills), "
        "including all immutable PROTECTED guardrails. "
        "Never run BigQuery jobs for metric formulas or partition rules—resolve those directly "
        "from the mounted skill definitions (and knowledge-catalog-mcp if attached). "
        "When live BigQuery rows are requested, build and execute /tmp/bq_runner.py (mirrored "
        f"to ./bq_runner.py) inside code_execution against https://bigquery.googleapis.com "
        f"(billing project '{project_id}', dryRun<=100MB first, _TABLE_SUFFIX required, "
        "no SELECT *); outbound Authorization headers are injected automatically by the "
        "sandbox egress proxy."
    )

    tools: list[dict[str, Any]] = [{"type": "code_execution"}]
    if catalog_mcp_url:
        tools.append(
            {
                "type": "mcp_server",
                "name": "knowledge-catalog-mcp",
                "url": catalog_mcp_url,
            }
        )

    skill_source = build_skill_source(
        skill_gcs_bucket=skill_gcs_bucket,
        skill_resource_name=skill_resource_name,
    )

    spec: dict[str, Any] = {
        "id": agent_id,
        "base_agent": base_agent,
        "description": (
            "Tokenomics-governed analytics agent that mounts SKILL.md and builds its own "
            "sandboxed BigQuery REST CLI."
        ),
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

    if model is not None or max_total_tokens is not None:
        agent_config: dict[str, Any] = {"type": "antigravity"}
        if model is not None:
            agent_config["model"] = model
        if max_total_tokens is not None:
            agent_config["max_total_tokens"] = max_total_tokens
        spec["agent_config"] = agent_config

    return spec


def wait_for_agent_ready(client: Any, agent_id: str, timeout_sec: int = 45) -> Any:
    """Polls `client.agents.get` while asynchronous agent creation completes."""
    deadline = time.monotonic() + timeout_sec
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            details = client.agents.get(id=agent_id)
            if getattr(details, "id", None) == agent_id:
                return details
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
        time.sleep(3)
    if last_exc is not None:
        raise RuntimeError(f"Timed out waiting for agent '{agent_id}': {last_exc}") from last_exc
    return client.agents.get(id=agent_id)


def run_step1(agent_id: str | None = None) -> str:
    """Provisions the Managed Agent on `locations/global` and saves `.lab_state.json`."""
    load_dotenv(LAB_DIR / ".env")
    from google import genai

    project_id = resolve_project_id()
    catalog_mcp_url = os.getenv("CATALOG_MCP_URL") or None
    base_agent = os.getenv("MANAGED_BASE_AGENT", DEFAULT_BASE_AGENT)
    chosen_id = (
        agent_id
        or os.getenv("MANAGED_AGENT_ID")
        or f"tokenomics-analytics-{uuid.uuid4().hex[:8]}"
    )
    model = os.getenv("MANAGED_AGENT_MODEL") or None
    max_tokens_raw = os.getenv("MANAGED_AGENT_MAX_TOKENS")
    max_total_tokens = int(max_tokens_raw) if max_tokens_raw else None
    skill_gcs_bucket = os.getenv("AGENT_SKILL_GCS_BUCKET") or None
    skill_resource_name = os.getenv("SKILL_RESOURCE_NAME") or None

    client = genai.Client(enterprise=True, project=project_id, location="global")
    spec = build_agent_spec(
        agent_id=chosen_id,
        project_id=project_id,
        catalog_mcp_url=catalog_mcp_url,
        base_agent=base_agent,
        model=model,
        max_total_tokens=max_total_tokens,
        skill_gcs_bucket=skill_gcs_bucket,
        skill_resource_name=skill_resource_name,
    )

    skill_mode = spec["base_environment"]["sources"][0]["type"]
    print("=== Step 1: Creating Managed Agent (`client.agents.create`) ===")
    print(f"  Project:      {project_id}")
    print(f"  Agent ID:     {chosen_id}")
    print(f"  Base Agent:   {base_agent}")
    print(f"  Skill Source: {skill_mode} (./skills/tokenomics-analytics/SKILL.md)")

    op = client.agents.create(**spec)
    print(f"  Operation:    {getattr(op, 'name', op)}")

    details = wait_for_agent_ready(client, chosen_id)
    tools_summary = [
        getattr(t, "type", str(t)) for t in (getattr(details, "tools", None) or [])
    ]
    save_lab_state({"project_id": project_id, "agent_id": details.id})

    print("\n[OK] Managed Agent Ready!")
    print(f"  agent_id = {details.id}")
    print(f"  tools    = {tools_summary}")
    print("\nNext step: python step2_first_interaction.py")
    return details.id


def main() -> None:
    parser = argparse.ArgumentParser(description="Step 1: Create the Managed Agent.")
    parser.add_argument("--agent-id", help="Optional custom ID for the Managed Agent.")
    args = parser.parse_args()
    run_step1(agent_id=args.agent_id)


if __name__ == "__main__":
    main()
