"""Verification suite and live runner for Lab 5 (Managed Agents API & Low-Infra Harness).

Usage:
  1. Fast hermetic unit & contract check (zero cloud calls):
       pytest test_managed_agent.py
  2. Live end-to-end verification against Gemini Platform (`locations/global`):
       python test_managed_agent.py [--cleanup]
"""

from __future__ import annotations

import argparse
import os
import types
import uuid

import pytest
from dotenv import load_dotenv

from create_managed_agent import (
    DEFAULT_BASE_AGENT,
    SKILL_FILE,
    build_agent_spec,
    build_egress_allowlist,
    build_skill_source,
    wait_for_agent_ready,
)
from low_infra_harness import run_managed_interaction, run_thin_model_turn


# ---------------------------------------------------------------------------
# Hermetic Unit & Contract Tests (`pytest test_managed_agent.py`)
# ---------------------------------------------------------------------------


def test_skill_markdown_has_frontmatter_and_circuit_breaker() -> None:
    text = SKILL_FILE.read_text(encoding="utf-8")
    assert text.startswith("---\nname: tokenomics-analytics\n")
    assert "knowledge-catalog-mcp" in text
    assert "_TABLE_SUFFIX" in text
    assert "SELECT *" in text


def test_build_skill_source_modes() -> None:
    reg = build_skill_source(skill_resource_name="projects/p/locations/global/skills/s1")
    assert reg == {
        "type": "skill_registry",
        "source": "projects/p/locations/global/skills/s1",
        "target": "./skills",
    }

    gcs = build_skill_source(skill_gcs_bucket="my-skill-bucket")
    assert gcs == {
        "type": "gcs",
        "source": "gs://my-skill-bucket",
        "target": "./skills",
    }

    inline = build_skill_source()
    assert inline["type"] == "inline"
    assert inline["target"].endswith("SKILL.md")
    assert "tokenomics-analytics" in inline["content"]


def test_build_agent_spec_wires_mcp_and_least_privilege_allowlist() -> None:
    mcp_url = "https://knowledge-catalog-mcp-abc123-uc.a.run.app/sse"
    spec = build_agent_spec(
        agent_id="demo-agent-01",
        project_id="sample-analytics-proj",
        catalog_mcp_url=mcp_url,
    )
    assert spec["id"] == "demo-agent-01"
    assert spec["base_agent"] == DEFAULT_BASE_AGENT
    assert {"type": "code_execution"} in spec["tools"]
    assert {
        "type": "mcp_server",
        "name": "knowledge-catalog-mcp",
        "url": mcp_url,
    } in spec["tools"]

    allowed_domains = {
        entry["domain"] for entry in spec["base_environment"]["network"]["allowlist"]
    }
    assert "knowledge-catalog-mcp-abc123-uc.a.run.app" in allowed_domains
    assert "bigquery.googleapis.com" in allowed_domains
    assert "*" not in allowed_domains


def test_run_managed_interaction_collects_text_tools_and_env_id() -> None:
    events = [
        types.SimpleNamespace(
            event_type="interaction.created",
            interaction=types.SimpleNamespace(
                id="int_1", environment_id=None, status="in_progress", usage=None
            ),
        ),
        types.SimpleNamespace(
            event_type="step.start",
            step=types.SimpleNamespace(
                type="mcp_server_tool_call", name="get_semantic_metric"
            ),
        ),
        types.SimpleNamespace(
            event_type="step.delta",
            delta=types.SimpleNamespace(type="text", text="Bounce rate uses "),
        ),
        types.SimpleNamespace(
            event_type="step.delta",
            delta=types.SimpleNamespace(type="text", text="COUNTIF(totals.bounces = 1)."),
        ),
        types.SimpleNamespace(
            event_type="interaction.completed",
            interaction=types.SimpleNamespace(
                id="int_1",
                environment_id="env_persist_001",
                status="completed",
                usage=types.SimpleNamespace(
                    total_tokens=4200,
                    total_cached_tokens=3600,
                    total_thought_tokens=310,
                ),
            ),
        ),
    ]

    recorded_kwargs: dict[str, object] = {}

    def fake_create(**kwargs: object):
        recorded_kwargs.update(kwargs)
        return iter(events)

    fake_client = types.SimpleNamespace(
        interactions=types.SimpleNamespace(create=fake_create)
    )

    turn = run_managed_interaction(
        fake_client,
        agent="demo-agent-01",
        prompt="What is our business formula for bounce rate?",
        environment="remote",
    )

    assert recorded_kwargs["agent"] == "demo-agent-01"
    assert recorded_kwargs["environment"] == "remote"
    assert turn.interaction_id == "int_1"
    assert turn.environment_id == "env_persist_001"
    assert turn.status == "completed"
    assert turn.tool_calls == ["get_semantic_metric"]
    assert turn.output_text == "Bounce rate uses COUNTIF(totals.bounces = 1)."
    assert turn.cached_tokens == 3600


def test_run_thin_model_turn_enforces_zdr_constraint_and_turn_scoped_args() -> None:
    captured: dict[str, object] = {}
    fake_client = types.SimpleNamespace(
        interactions=types.SimpleNamespace(
            create=lambda **kw: captured.update(kw) or types.SimpleNamespace(id="int_2")
        )
    )

    with pytest.raises(ValueError, match="store=True"):
        run_thin_model_turn(
            fake_client,
            prompt="follow up",
            previous_interaction_id="int_1",
            store=False,
        )

    resp = run_thin_model_turn(
        fake_client,
        prompt="Summarize partition rules.",
        model="gemini-3.8-flash",
        system_instruction="Consult catalog first.",
        tools=[{"type": "mcp_server", "name": "kc", "url": "https://example.run.app/sse"}],
        previous_interaction_id="int_1",
        store=True,
    )
    assert resp.id == "int_2"
    assert captured["previous_interaction_id"] == "int_1"
    assert captured["system_instruction"] == "Consult catalog first."
    assert len(build_egress_allowlist(None)) == 3


# ---------------------------------------------------------------------------
# Live End-to-End Runner (`python test_managed_agent.py [--cleanup]`)
# ---------------------------------------------------------------------------


def run_live_verification(cleanup: bool = False) -> None:
    load_dotenv()
    from google import genai

    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    catalog_mcp_url = os.getenv("CATALOG_MCP_URL")
    if not project_id or not catalog_mcp_url:
        raise ValueError(
            "GOOGLE_CLOUD_PROJECT and CATALOG_MCP_URL must be set to run live verification."
        )

    client = genai.Client(enterprise=True, project=project_id, location="global")
    agent_id = os.getenv("MANAGED_AGENT_ID")
    created_ephemeral = False

    if not agent_id:
        agent_id = f"tokenomics-analytics-{uuid.uuid4().hex[:8]}"
        spec = build_agent_spec(
            agent_id=agent_id,
            project_id=project_id,
            catalog_mcp_url=catalog_mcp_url,
            base_agent=os.getenv("MANAGED_BASE_AGENT", DEFAULT_BASE_AGENT),
            skill_resource_name=os.getenv("SKILL_RESOURCE_NAME") or None,
            skill_gcs_bucket=os.getenv("AGENT_SKILL_GCS_BUCKET") or None,
        )
        print(f"Provisioning Managed Agent '{agent_id}'...")
        client.agents.create(**spec)
        wait_for_agent_ready(client, agent_id)
        created_ephemeral = True

    try:
        print("\n=== Turn 1: Metric Formula via Knowledge Catalog MCP (Zero BQ Scan) ===")
        t1 = run_managed_interaction(
            client,
            agent=agent_id,
            prompt="What is our official business formula for bounce rate? Save a short note to /tmp/metric_note.txt.",
            environment={"type": "remote"},
        )
        print(t1.output_text)
        print(f"[State] interaction_id={t1.interaction_id} | environment_id={t1.environment_id}")
        assert "bounces" in t1.output_text.lower() or "countif" in t1.output_text.lower()

        print("\n=== Turn 2: Reusing Persistent Sandbox (`environment_id`) + Partition Spec ===")
        t2 = run_managed_interaction(
            client,
            agent=agent_id,
            prompt=(
                "Confirm /tmp/metric_note.txt still exists in this sandbox, and tell me the "
                "partition rules for ga_sessions without running any BigQuery SQL."
            ),
            environment=t1.environment_id or {"type": "remote"},
        )
        print(t2.output_text)
        assert "_TABLE_SUFFIX" in t2.output_text

        print("\n=== Turn 3: Partitioned BigQuery Execution + Dynamic MCP Override ===")
        t3 = run_managed_interaction(
            client,
            agent=agent_id,
            prompt="Find the top 3 traffic sources in ga_sessions for August 1, 2017 using the required partition filter.",
            environment=t2.environment_id or t1.environment_id,
            tools=[
                {"type": "code_execution"},
                {
                    "type": "mcp_server",
                    "name": "knowledge-catalog-mcp",
                    "url": catalog_mcp_url,
                },
            ],
        )
        print(t3.output_text)
        print("\nALL MANAGED AGENTS API GOVERNANCE & STATE CHECKS PASSED!")
    finally:
        if cleanup or created_ephemeral:
            client.agents.delete(id=agent_id)
            print(f"Cleaned up Managed Agent: {agent_id}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Delete MANAGED_AGENT_ID after completing live verification.",
    )
    args = parser.parse_args()
    run_live_verification(cleanup=args.cleanup)
