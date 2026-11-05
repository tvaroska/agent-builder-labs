"""Deletes a custom Managed Agent (and optional remote sandbox) from Gemini Platform."""

from __future__ import annotations

import argparse
import os

from dotenv import load_dotenv


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Delete a Managed Agent and/or persistent remote sandbox environment."
    )
    parser.add_argument(
        "positional_agent_id",
        nargs="?",
        help="Agent ID to delete (falls back to --agent-id or MANAGED_AGENT_ID).",
    )
    parser.add_argument("--agent-id", dest="agent_id", help="Agent ID to delete.")
    parser.add_argument(
        "--env-id",
        default=os.getenv("MANAGED_ENVIRONMENT_ID"),
        help="Optional persistent remote sandbox environment_id to delete.",
    )
    args = parser.parse_args()

    from google import genai

    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    agent_id = args.agent_id or args.positional_agent_id or os.getenv("MANAGED_AGENT_ID")

    if not project_id:
        raise ValueError("GOOGLE_CLOUD_PROJECT is required.")
    if not agent_id and not args.env_id:
        raise ValueError(
            "Pass an agent_id / --env-id argument or export MANAGED_AGENT_ID."
        )

    client = genai.Client(enterprise=True, project=project_id, location="global")

    if agent_id:
        client.agents.delete(id=agent_id)
        print(f"Deleted Managed Agent: {agent_id}")

    if args.env_id:
        client.environments.delete(id=args.env_id)
        print(f"Deleted Remote Environment: {args.env_id}")

    listed = client.agents.list()
    remaining = [a.id for a in (getattr(listed, "agents", None) or [])]
    print(f"Remaining project agents: {remaining or 'None'}")


if __name__ == "__main__":
    main()
