"""Minimal stateful interaction harnesses on the Gemini Platform.

Contrasts two low-infrastructure patterns that avoid deploying custom agent
containers or multi-service orchestration frameworks:
1. Managed Agent Harness (`run_managed_interaction`): delegates tool loops,
   sandbox code building/execution, and skill discovery to a server-managed
   Agent resource, using host egress proxy `transform` headers so sandboxed
   Python tools can query BigQuery REST APIs with zero MCP server and zero
   secrets inside sandbox memory.
2. Thin Model Harness (`run_thin_model_turn`): drives a foundation model over
   `POST .../interactions` using `previous_interaction_id` so `thought_signature`
   and prefix-cache affinity stay server-side without resending history arrays.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlparse


@dataclass
class ManagedTurnResult:
    interaction_id: str | None = None
    environment_id: str | None = None
    status: str | None = None
    output_text: str = ""
    tool_calls: list[str] = field(default_factory=list)
    total_tokens: int | None = None
    cached_tokens: int | None = None
    thought_tokens: int | None = None


def mint_bigquery_access_token() -> str:
    """Mints a short-lived BigQuery OAuth access token from host ADC."""
    import google.auth
    from google.auth.transport.requests import Request

    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/bigquery"]
    )
    creds.refresh(Request())
    if not creds.token:
        raise RuntimeError("Failed to mint OAuth access token from google.auth.default().")
    return creds.token


def build_bigquery_proxy_environment(
    project_id: str,
    *,
    bq_access_token: str | None = None,
    catalog_mcp_url: str | None = None,
    environment_id: str | None = None,
    sources: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Builds a remote sandbox spec with zero-secret proxy auth to BigQuery.

    When `bq_access_token` is supplied, the Managed Agents host egress proxy
    injects `Authorization` and `X-Goog-User-Project` headers on outbound TLS
    connections to `bigquery.googleapis.com`. Python scripts built and executed
    by the agent inside `code_execution` call the BigQuery REST API directly
    without an MCP server and without any token present in sandbox env or disk.
    """
    bq_rule: dict[str, Any] = {"domain": "bigquery.googleapis.com"}
    if bq_access_token:
        bq_rule["transform"] = {
            "Authorization": f"Bearer {bq_access_token}",
            "X-Goog-User-Project": project_id,
        }

    allowlist: list[dict[str, Any]] = [bq_rule]
    if catalog_mcp_url:
        host = urlparse(catalog_mcp_url).hostname
        if host and host != "bigquery.googleapis.com":
            allowlist.append({"domain": host})

    env_spec: dict[str, Any] = {
        "type": "remote",
        "env": {
            "GOOGLE_CLOUD_PROJECT": project_id,
        },
        "network": {
            "allowlist": allowlist,
        },
    }
    if environment_id:
        env_spec["environment_id"] = environment_id
    if sources:
        env_spec["sources"] = sources
    return env_spec


def run_managed_interaction(
    client: Any,
    *,
    agent: str,
    prompt: str,
    environment: str | dict[str, Any] | None = None,
    tools: list[dict[str, Any]] | None = None,
    store: bool = True,
    background: bool = True,
    on_text_delta: Callable[[str], None] | None = None,
) -> ManagedTurnResult:
    """Streams a single turn against a Managed Agent and extracts state handles.

    Agent interactions are backed by a durable server-side task; passing the
    returned `environment_id` into subsequent turns reattaches to the same
    7-day rolling TTL Linux sandbox (preserving agent-built scripts and files).
    """
    kwargs: dict[str, Any] = {
        "agent": agent,
        "input": prompt,
        "stream": True,
        "background": background,
        "store": store,
    }
    if environment is not None:
        kwargs["environment"] = environment
    if tools is not None:
        kwargs["tools"] = tools

    result = ManagedTurnResult()
    chunks: list[str] = []

    stream = client.interactions.create(**kwargs)
    for event in stream:
        event_type = getattr(event, "event_type", "")

        interaction = getattr(event, "interaction", None)
        if interaction is not None:
            if getattr(interaction, "id", None):
                result.interaction_id = interaction.id
            if getattr(interaction, "environment_id", None):
                result.environment_id = interaction.environment_id
            if getattr(interaction, "status", None):
                result.status = interaction.status
            usage = getattr(interaction, "usage", None)
            if usage is not None:
                result.total_tokens = getattr(usage, "total_tokens", None)
                result.cached_tokens = getattr(usage, "total_cached_tokens", None)
                result.thought_tokens = getattr(usage, "total_thought_tokens", None)

        if event_type == "step.start":
            step = getattr(event, "step", None)
            step_type = getattr(step, "type", "")
            if step_type in (
                "function_call",
                "mcp_server_tool_call",
                "code_execution_call",
            ):
                name = getattr(step, "name", None) or step_type
                result.tool_calls.append(name)

        elif event_type == "step.delta":
            delta = getattr(event, "delta", None)
            if getattr(delta, "type", "") == "text":
                text = getattr(delta, "text", "") or ""
                if text:
                    chunks.append(text)
                    if on_text_delta is not None:
                        on_text_delta(text)

    result.output_text = "".join(chunks)
    return result


def run_thin_model_turn(
    client: Any,
    *,
    prompt: str | list[dict[str, Any]],
    model: str = "gemini-3.8-flash",
    system_instruction: str | None = None,
    tools: list[Any] | None = None,
    previous_interaction_id: str | None = None,
    store: bool = True,
) -> Any:
    """Executes one stateful model turn over the Interactions API.

    Note: `previous_interaction_id` chains conversation `steps[]` (including
    encrypted `thought` signatures for ~90% multi-turn prefix cache hit rates),
    whereas `system_instruction` and `tools` are turn-scoped and must be passed
    on every turn. Setting `store=False` enables stateless Zero Data Retention
    (ZDR) mode, which cannot be combined with `previous_interaction_id`.
    """
    if not store and previous_interaction_id is not None:
        raise ValueError("previous_interaction_id requires store=True.")

    kwargs: dict[str, Any] = {
        "model": model,
        "input": prompt,
        "store": store,
    }
    if system_instruction is not None:
        kwargs["system_instruction"] = system_instruction
    if tools is not None:
        kwargs["tools"] = tools
    if previous_interaction_id is not None:
        kwargs["previous_interaction_id"] = previous_interaction_id

    return client.interactions.create(**kwargs)
