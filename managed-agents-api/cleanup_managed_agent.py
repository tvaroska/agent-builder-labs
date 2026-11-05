"""Deletes a custom Managed Agent from the Gemini Platform control plane."""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv


def main() -> None:
    load_dotenv()
    from google import genai

    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    agent_id = sys.argv[1] if len(sys.argv) > 1 else os.getenv("MANAGED_AGENT_ID")

    if not project_id:
        raise ValueError("GOOGLE_CLOUD_PROJECT is required.")
    if not agent_id:
        raise ValueError("Pass an agent_id argument or export MANAGED_AGENT_ID.")

    client = genai.Client(enterprise=True, project=project_id, location="global")
    client.agents.delete(id=agent_id)
    print(f"Deleted Managed Agent: {agent_id}")

    listed = client.agents.list()
    remaining = [a.id for a in (getattr(listed, "agents", None) or [])]
    print(f"Remaining project agents: {remaining or 'None'}")


if __name__ == "__main__":
    main()
