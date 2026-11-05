"""Step 2: First Interaction with the Managed Agent (`client.interactions.create`).

Starts Session A against the Managed Agent created in Step 1:
- Looks up the official `bounce_rate` SQL formula and `ga_sessions_*` `_TABLE_SUFFIX`
  partition rule from the mounted `SKILL.md` with zero BigQuery scan.
- Builds and unit-tests `/tmp/bq_runner.py` (mirrored to `./bq_runner.py`) inside
  the managed `code_execution` sandbox.
- Captures `interaction_id` and `environment_id` into `.lab_state.json` for Step 3.
"""

from __future__ import annotations

import argparse
import os

from dotenv import load_dotenv
from low_infra_harness import (
    LAB_DIR,
    ManagedTurnResult,
    build_bigquery_proxy_environment,
    inspect_sandbox_environment,
    load_lab_state,
    resolve_project_id,
    run_managed_interaction,
    save_lab_state,
)


def run_step2(agent_id: str | None = None) -> ManagedTurnResult:
    """Executes the first interaction against `agent_id` and saves session handles."""
    load_dotenv(LAB_DIR / ".env")
    from google import genai

    project_id = resolve_project_id()
    state = load_lab_state()
    resolved_agent_id = (
        agent_id or os.getenv("MANAGED_AGENT_ID") or state.get("agent_id")
    )
    if not resolved_agent_id:
        raise ValueError(
            "No Managed Agent ID found. Run 'python step1_create_agent.py' first."
        )

    catalog_mcp_url = os.getenv("CATALOG_MCP_URL") or None
    client = genai.Client(enterprise=True, project=project_id, location="global")

    print("=== Step 2: First Interaction with Managed Agent (`client.interactions.create`) ===")
    print(f"  agent_id = {resolved_agent_id}")

    turn1 = run_managed_interaction(
        client,
        agent=resolved_agent_id,
        prompt=(
            "1. From your mounted tokenomics-analytics skill (without running BigQuery SQL), "
            "state our official SQL formula for bounce_rate and the required partition key "
            "for bigquery-public-data.google_analytics_sample.ga_sessions_*.\n"
            "2. In code_execution, write /tmp/bq_runner.py (and copy it to ./bq_runner.py in "
            "the workspace) following the PROTECTED rules in the skill (rejecting SELECT *, "
            "requiring _TABLE_SUFFIX on ga_sessions, running dryRun<=100MB first, and calling "
            "https://bigquery.googleapis.com/bigquery/v2/projects/${GOOGLE_CLOUD_PROJECT}/queries "
            "using Python stdlib urllib with no Authorization header in code).\n"
            "3. Run a local Python self-test confirming /tmp/bq_runner.py blocks "
            "'SELECT * FROM ga_sessions_*' and blocks queries missing _TABLE_SUFFIX before "
            "making any HTTP request."
        ),
        environment=build_bigquery_proxy_environment(
            project_id,
            catalog_mcp_url=catalog_mcp_url,
        ),
    )

    print("\n--- Agent Response ---")
    print(turn1.output_text)

    if turn1.environment_id:
        snap = inspect_sandbox_environment(client, turn1.environment_id)
        print(
            f"\n[Sandbox Snapshot] environment_id={snap['id']} | "
            f"status={snap['status']} | files={snap['files']}"
        )

    save_lab_state(
        {
            "agent_id": resolved_agent_id,
            "session_a_interaction_id": turn1.interaction_id,
            "session_a_environment_id": turn1.environment_id,
        },
        env_id=turn1.environment_id,
    )

    print("\n[OK] Step 2 Complete — Saved Session A Handles:")
    print(f"  interaction_id = {turn1.interaction_id}")
    print(f"  environment_id = {turn1.environment_id}")
    print("\nNext step: python step3_followup_interaction.py")
    return turn1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Step 2: Run the first interaction against the Managed Agent."
    )
    parser.add_argument("--agent-id", help="Override Managed Agent ID from Step 1.")
    args = parser.parse_args()
    run_step2(agent_id=args.agent_id)


if __name__ == "__main__":
    main()
