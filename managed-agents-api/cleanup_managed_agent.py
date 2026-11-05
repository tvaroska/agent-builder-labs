"""Deletes the Managed Agent and remote sandboxes created during Steps 1–4."""

from __future__ import annotations

import argparse
import os

from dotenv import load_dotenv
from low_infra_harness import (
    LAB_DIR,
    clear_lab_state,
    load_lab_state,
    resolve_project_id,
)


def main() -> None:
    load_dotenv(LAB_DIR / ".env")
    parser = argparse.ArgumentParser(
        description="Delete the Managed Agent and persistent remote sandboxes from this lab."
    )
    parser.add_argument(
        "positional_agent_id",
        nargs="?",
        help="Optional Agent ID to delete (defaults to .lab_state.json or MANAGED_AGENT_ID).",
    )
    parser.add_argument("--agent-id", dest="agent_id", help="Agent ID to delete.")
    parser.add_argument(
        "--env-id",
        action="append",
        dest="env_ids",
        help="Optional remote sandbox environment_id(s) to delete.",
    )
    args = parser.parse_args()

    from google import genai

    project_id = resolve_project_id()
    state = load_lab_state()
    agent_id = (
        args.agent_id
        or args.positional_agent_id
        or os.getenv("MANAGED_AGENT_ID")
        or state.get("agent_id")
    )

    env_ids: list[str] = []
    for eid in (args.env_ids or []) + list(state.get("environments") or []):
        if eid and eid not in env_ids:
            env_ids.append(eid)
    env_from_var = os.getenv("MANAGED_ENVIRONMENT_ID")
    if env_from_var and env_from_var not in env_ids:
        env_ids.append(env_from_var)

    if not agent_id and not env_ids:
        raise ValueError(
            "Nothing to clean up: no .lab_state.json found and no --agent-id / --env-id supplied."
        )

    client = genai.Client(enterprise=True, project=project_id, location="global")

    if agent_id:
        client.agents.delete(id=agent_id)
        print(f"Deleted Managed Agent: {agent_id}")

    for eid in env_ids:
        try:
            client.environments.delete(id=eid)
            print(f"Deleted Remote Environment: {eid}")
        except Exception as exc:  # noqa: BLE001
            print(f"Note deleting environment {eid}: {exc}")

    clear_lab_state()

    listed = client.agents.list()
    remaining = [a.id for a in (getattr(listed, "agents", None) or [])]
    print(f"Remaining project agents: {remaining or 'None'}")


if __name__ == "__main__":
    main()
