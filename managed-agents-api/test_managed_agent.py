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
from create_managed_agent import (
    DEFAULT_BASE_AGENT,
    SKILL_FILE,
    build_agent_spec,
    build_egress_allowlist,
    build_skill_source,
    wait_for_agent_ready,
)
from dotenv import load_dotenv
from low_infra_harness import (
    build_bigquery_proxy_environment,
    inspect_sandbox_environment,
    mint_bigquery_access_token,
    run_managed_interaction,
    run_thin_model_turn,
)

# ---------------------------------------------------------------------------
# Hermetic Unit & Contract Tests (`pytest test_managed_agent.py`)
# ---------------------------------------------------------------------------


def test_skill_markdown_has_frontmatter_protected_block_and_self_built_bq_cli() -> None:
    text = SKILL_FILE.read_text(encoding="utf-8")
    assert text.startswith("---\nname: tokenomics-analytics\n")
    assert "<!-- PROTECTED:START -->" in text
    assert "<!-- PROTECTED:END -->" in text
    assert "knowledge-catalog-mcp" in text
    assert "/tmp/bq_runner.py" in text
    assert "./bq_runner.py" in text
    assert "dryRun" in text
    assert "_TABLE_SUFFIX" in text
    assert "SELECT" in text


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


def test_build_agent_spec_wires_mcp_allowlist_and_optional_agent_config() -> None:
    mcp_url = "https://knowledge-catalog-mcp-abc123-uc.a.run.app/sse"
    spec = build_agent_spec(
        agent_id="demo-agent-01",
        project_id="sample-analytics-proj",
        catalog_mcp_url=mcp_url,
        model="gemini-3.8-flash",
        max_total_tokens=65536,
    )
    assert spec["id"] == "demo-agent-01"
    assert spec["base_agent"] == DEFAULT_BASE_AGENT
    assert spec["agent_config"] == {
        "type": "antigravity",
        "model": "gemini-3.8-flash",
        "max_total_tokens": 65536,
    }
    assert {"type": "code_execution"} in spec["tools"]
    assert {
        "type": "mcp_server",
        "name": "knowledge-catalog-mcp",
        "url": mcp_url,
    } in spec["tools"]
    assert spec["base_environment"]["env"] == {
        "GOOGLE_CLOUD_PROJECT": "sample-analytics-proj"
    }

    allowed_domains = {
        entry["domain"] for entry in spec["base_environment"]["network"]["allowlist"]
    }
    assert allowed_domains == {
        "bigquery.googleapis.com",
        "knowledge-catalog-mcp-abc123-uc.a.run.app",
    }


def test_build_bigquery_proxy_environment_injects_headers_outside_sandbox() -> None:
    env_spec = build_bigquery_proxy_environment(
        "qwiklabs-gcp-01-demo",
        bq_access_token="stub-ephemeral-token",
        catalog_mcp_url="https://knowledge-catalog-mcp-xyz-uc.a.run.app/sse",
        environment_id="env_persist_001",
    )
    assert env_spec["type"] == "remote"
    assert env_spec["environment_id"] == "env_persist_001"
    # Token must NEVER appear in sandbox environment variables
    assert env_spec["env"] == {"GOOGLE_CLOUD_PROJECT": "qwiklabs-gcp-01-demo"}
    assert "stub-ephemeral-token" not in str(env_spec["env"])

    allowlist = env_spec["network"]["allowlist"]
    bq_entry = next(r for r in allowlist if r["domain"] == "bigquery.googleapis.com")
    assert bq_entry["transform"] == {
        "Authorization": "Bearer stub-ephemeral-token",
        "X-Goog-User-Project": "qwiklabs-gcp-01-demo",
    }
    assert {"domain": "knowledge-catalog-mcp-xyz-uc.a.run.app"} in allowlist


def test_run_managed_interaction_hydrates_env_id_via_get_fallback() -> None:
    # Simulate SSE stream where InteractionSseEventInteraction omits environment_id
    events = [
        types.SimpleNamespace(
            event_type="interaction.created",
            interaction=types.SimpleNamespace(
                id="int_1", status="in_progress", usage=None
            ),
        ),
        types.SimpleNamespace(
            event_type="step.start",
            step=types.SimpleNamespace(type="code_execution_call", name=None),
        ),
        types.SimpleNamespace(
            event_type="step.delta",
            delta=types.SimpleNamespace(type="text", text="Top source: "),
        ),
        types.SimpleNamespace(
            event_type="step.delta",
            delta=types.SimpleNamespace(type="text", text="(direct) with 1,240 sessions."),
        ),
        types.SimpleNamespace(
            event_type="interaction.completed",
            interaction=types.SimpleNamespace(
                id="int_1",
                status="completed",
                usage=types.SimpleNamespace(
                    total_input_tokens=4000,
                    total_output_tokens=200,
                    total_tokens=4200,
                    total_cached_tokens=3600,
                    total_thought_tokens=310,
                ),
            ),
        ),
    ]

    recorded_kwargs: dict[str, object] = {}
    fetched_ids: list[str] = []

    def fake_create(**kwargs: object):
        recorded_kwargs.update(kwargs)
        return iter(events)

    def fake_get(interaction_id: str):
        fetched_ids.append(interaction_id)
        return types.SimpleNamespace(
            id=interaction_id,
            environment_id="env_persist_001",
            status="completed",
            usage=None,
        )

    fake_client = types.SimpleNamespace(
        interactions=types.SimpleNamespace(create=fake_create, get=fake_get)
    )

    turn = run_managed_interaction(
        fake_client,
        agent="demo-agent-01",
        prompt="Run /tmp/bq_runner.py for 20170801.",
        environment={"type": "remote", "environment_id": "env_persist_001"},
        tools=[{"type": "code_execution"}],
    )

    assert recorded_kwargs["agent"] == "demo-agent-01"
    assert recorded_kwargs["tools"] == [{"type": "code_execution"}]
    assert fetched_ids == ["int_1"]
    assert turn.interaction_id == "int_1"
    assert turn.environment_id == "env_persist_001"
    assert turn.status == "completed"
    assert turn.tool_calls == ["code_execution_call"]
    assert turn.output_text == "Top source: (direct) with 1,240 sessions."
    assert turn.cached_tokens == 3600
    assert turn.cache_hit_ratio == pytest.approx(0.90)


def test_run_managed_interaction_raises_on_error_event_or_failed_status() -> None:
    error_events = [
        types.SimpleNamespace(
            event_type="interaction.created",
            interaction=types.SimpleNamespace(
                id="int_err", environment_id="env_1", status="in_progress", usage=None
            ),
        ),
        types.SimpleNamespace(
            event_type="error",
            error="Sandbox quota exhausted",
        ),
    ]
    fake_client = types.SimpleNamespace(
        interactions=types.SimpleNamespace(create=lambda **_kw: iter(error_events))
    )

    with pytest.raises(RuntimeError, match="Sandbox quota exhausted"):
        run_managed_interaction(fake_client, agent="demo-agent-01", prompt="Run query.")


def test_run_managed_interaction_chains_previous_interaction_id_and_blocks_zdr() -> None:
    from google.genai._gaos.types.interactions import CreateAgentInteraction

    captured_calls: list[dict[str, object]] = []

    def fake_create(**kwargs: object):
        # Validate payload against the official google-genai CreateAgentInteraction schema
        CreateAgentInteraction.model_validate(kwargs)
        captured_calls.append(dict(kwargs))
        return iter(
            [
                types.SimpleNamespace(
                    event_type="interaction.completed",
                    interaction=types.SimpleNamespace(
                        id="int_followup_2",
                        previous_interaction_id=kwargs.get("previous_interaction_id"),
                        environment_id="env_persist_001",
                        status="completed",
                        usage=None,
                    ),
                )
            ]
        )

    fake_client = types.SimpleNamespace(
        interactions=types.SimpleNamespace(create=fake_create)
    )

    with pytest.raises(ValueError, match="store=True"):
        run_managed_interaction(
            fake_client,
            agent="demo-agent-01",
            prompt="Follow up in ZDR mode",
            previous_interaction_id="int_1",
            store=False,
        )

    followup = run_managed_interaction(
        fake_client,
        agent="demo-agent-01",
        prompt="Using the partition rule from our previous turn, build /tmp/bq_runner.py.",
        previous_interaction_id="int_1",
        environment={"type": "remote", "environment_id": "env_persist_001"},
    )
    assert len(captured_calls) == 1
    assert captured_calls[0]["agent"] == "demo-agent-01"
    assert captured_calls[0]["previous_interaction_id"] == "int_1"
    assert followup.interaction_id == "int_followup_2"
    assert followup.previous_interaction_id == "int_1"
    assert followup.environment_id == "env_persist_001"


def test_reusing_same_agent_starts_fresh_isolated_interaction_when_ids_omitted() -> None:
    captured_calls: list[dict[str, object]] = []

    def fake_create(**kwargs: object):
        call_num = len(captured_calls) + 1
        captured_calls.append(dict(kwargs))
        return iter(
            [
                types.SimpleNamespace(
                    event_type="interaction.completed",
                    interaction=types.SimpleNamespace(
                        id=f"int_session_{call_num}",
                        previous_interaction_id=kwargs.get("previous_interaction_id"),
                        environment_id=f"env_session_{call_num}",
                        status="completed",
                        usage=None,
                    ),
                )
            ]
        )

    fake_client = types.SimpleNamespace(
        interactions=types.SimpleNamespace(create=fake_create)
    )

    session_a = run_managed_interaction(
        fake_client,
        agent="demo-agent-01",
        prompt="Session A: bounce rate formula?",
    )
    session_b = run_managed_interaction(
        fake_client,
        agent="demo-agent-01",
        prompt="Session B: monthly active users formula?",
    )

    assert captured_calls[0]["agent"] == captured_calls[1]["agent"] == "demo-agent-01"
    assert "previous_interaction_id" not in captured_calls[1]
    assert "environment" not in captured_calls[1]
    assert session_b.previous_interaction_id is None
    assert session_a.interaction_id != session_b.interaction_id
    assert session_a.environment_id != session_b.environment_id


def test_inspect_sandbox_environment_returns_snapshot_files() -> None:
    fake_client = types.SimpleNamespace(
        environments=types.SimpleNamespace(
            get=lambda *, id: types.SimpleNamespace(
                id=id, status="READY", file_count=2, size_bytes=4096
            ),
            files=types.SimpleNamespace(
                list=lambda *, environment, path: types.SimpleNamespace(
                    files=[
                        types.SimpleNamespace(path="bq_runner.py", name="bq_runner.py"),
                        types.SimpleNamespace(
                            path=".agents/skills/tokenomics-analytics/SKILL.md",
                            name="SKILL.md",
                        ),
                    ]
                )
            ),
        )
    )
    snap = inspect_sandbox_environment(fake_client, "env_persist_001")
    assert snap["id"] == "env_persist_001"
    assert snap["status"] == "READY"
    assert "bq_runner.py" in snap["files"]


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
    assert build_egress_allowlist(None) == [{"domain": "bigquery.googleapis.com"}]


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
    created_envs: list[str] = []

    print("\n=== [Step 1/4] Create or Load Managed Agent (client.agents.create) ===")
    if not agent_id:
        agent_id = f"tokenomics-analytics-{uuid.uuid4().hex[:8]}"
        spec = build_agent_spec(
            agent_id=agent_id,
            project_id=project_id,
            catalog_mcp_url=catalog_mcp_url,
            base_agent=os.getenv("MANAGED_BASE_AGENT", DEFAULT_BASE_AGENT),
            model=os.getenv("MANAGED_AGENT_MODEL") or None,
            skill_resource_name=os.getenv("SKILL_RESOURCE_NAME") or None,
            skill_gcs_bucket=os.getenv("AGENT_SKILL_GCS_BUCKET") or None,
        )
        print(f"Provisioning Managed Agent '{agent_id}'...")
        client.agents.create(**spec)
        wait_for_agent_ready(client, agent_id)
        created_ephemeral = True
    else:
        details = wait_for_agent_ready(client, agent_id)
        print(f"Using pre-created Managed Agent '{details.id}' (base_agent={details.base_agent}).")

    try:
        print(
            "\n=== [Step 2/4] First Interaction via Interactions API "
            "(Session A — Initial Turn, Zero BQ Scan) ==="
        )
        t1 = run_managed_interaction(
            client,
            agent=agent_id,
            prompt=(
                "Look up our official business formula for bounce rate and the required partition "
                "key for ga_sessions in the Knowledge Catalog without running any BigQuery SQL."
            ),
            environment=build_bigquery_proxy_environment(
                project_id,
                catalog_mcp_url=catalog_mcp_url,
            ),
        )
        session_a_env_id = t1.environment_id
        if session_a_env_id:
            created_envs.append(session_a_env_id)
        print(t1.output_text)
        print(
            f"[Session A — Turn 1] interaction_id={t1.interaction_id} | "
            f"environment_id={session_a_env_id}"
        )
        assert "bounces" in t1.output_text.lower() or "countif" in t1.output_text.lower()
        assert "_table_suffix" in t1.output_text.lower()

        print(
            "\n=== [Step 3/4] Follow Up on Previous Interaction "
            "(Session A — Chained via previous_interaction_id + environment_id) ==="
        )
        print("--- [Step 3a] Build & Self-Test `/tmp/bq_runner.py` in Same Conversation & Sandbox ---")
        t2 = run_managed_interaction(
            client,
            agent=agent_id,
            previous_interaction_id=t1.interaction_id,
            prompt=(
                "Using the ga_sessions partition rule from our previous turn, write /tmp/bq_runner.py "
                "(and copy it to ./bq_runner.py in the workspace) in code_execution following the "
                "tokenomics-analytics skill (rejecting SELECT *, requiring _TABLE_SUFFIX on ga_sessions, "
                "running dryRun<=100MB first, and calling "
                "https://bigquery.googleapis.com/bigquery/v2/projects/${GOOGLE_CLOUD_PROJECT}/queries "
                "using Python stdlib urllib with no Authorization header in code). "
                "Then run a local self-test in Python confirming /tmp/bq_runner.py rejects "
                "'SELECT * FROM ga_sessions_*' and queries missing _TABLE_SUFFIX before any HTTP call."
            ),
            environment=build_bigquery_proxy_environment(
                project_id,
                catalog_mcp_url=catalog_mcp_url,
                environment_id=session_a_env_id,
            ),
        )
        session_a_env_id = t2.environment_id or session_a_env_id
        if session_a_env_id and session_a_env_id not in created_envs:
            created_envs.append(session_a_env_id)
        print(t2.output_text)
        print(
            f"[Session A — Turn 2] interaction_id={t2.interaction_id} "
            f"(prev={t2.previous_interaction_id}) | environment_id={session_a_env_id}"
        )
        assert "_TABLE_SUFFIX" in t2.output_text
        assert "code_execution_call" in t2.tool_calls

        if session_a_env_id:
            snap = inspect_sandbox_environment(client, session_a_env_id)
            print(
                f"[Sandbox Snapshot] env={snap['id']} status={snap['status']} files={snap['files']}"
            )

        print("--- [Step 3b] Follow-Up Query Execution Using Self-Built CLI (Zero MCP Attached!) ---")
        bq_token = mint_bigquery_access_token()
        t3 = run_managed_interaction(
            client,
            agent=agent_id,
            previous_interaction_id=t2.interaction_id,
            prompt=(
                "Now run ONLY your /tmp/bq_runner.py CLI in code_execution (no MCP tools) to find "
                "the top 3 traffic sources (trafficSource.source) by visit count in "
                "`bigquery-public-data.google_analytics_sample.ga_sessions_*` for August 1, 2017 "
                "(`_TABLE_SUFFIX = '20170801'`), and report both the dry-run bytes processed and exact counts."
            ),
            environment=build_bigquery_proxy_environment(
                project_id,
                bq_access_token=bq_token,
                environment_id=session_a_env_id,
            ),
            tools=[{"type": "code_execution"}],
        )
        print(t3.output_text)
        print(
            f"[Session A — Turn 3] interaction_id={t3.interaction_id} "
            f"(prev={t3.previous_interaction_id}) | environment_id={t3.environment_id}"
        )
        assert "code_execution_call" in t3.tool_calls
        assert "mcp_server_tool_call" not in t3.tool_calls

        print(
            "\n=== [Step 4/4] Reuse Same Agent for Next Independent Interaction "
            "(Session B — Fresh Conversation & Clean Isolated Sandbox) ==="
        )
        t4 = run_managed_interaction(
            client,
            agent=agent_id,
            prompt=(
                "What is our official Knowledge Catalog definition and SQL formula for Monthly Active "
                "Users (monthly-active-users)? Also check via code_execution whether /tmp/bq_runner.py "
                "exists in this fresh sandbox."
            ),
            environment=build_bigquery_proxy_environment(
                project_id,
                catalog_mcp_url=catalog_mcp_url,
            ),
        )
        if t4.environment_id and t4.environment_id not in created_envs:
            created_envs.append(t4.environment_id)
        print(t4.output_text)
        print(
            f"[Session B — Fresh Turn 1] interaction_id={t4.interaction_id} "
            f"(prev={t4.previous_interaction_id}) | environment_id={t4.environment_id}"
        )
        assert t4.previous_interaction_id is None
        assert "fullvisitorid" in t4.output_text.lower()
        if session_a_env_id and t4.environment_id:
            assert t4.environment_id != session_a_env_id

        print("\nALL 4 MANAGED AGENT LIFECYCLE STEPS & GOVERNANCE CHECKS PASSED!")
    finally:
        if cleanup or created_ephemeral:
            client.agents.delete(id=agent_id)
            print(f"Cleaned up Managed Agent: {agent_id}")
            if cleanup:
                for env_id in created_envs:
                    try:
                        client.environments.delete(id=env_id)
                        print(f"Cleaned up Remote Environment: {env_id}")
                    except Exception:  # noqa: BLE001, S110
                        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Delete MANAGED_AGENT_ID and its sandbox environment after live verification.",
    )
    args = parser.parse_args()
    run_live_verification(cleanup=args.cleanup)
