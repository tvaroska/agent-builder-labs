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
   and prefix-cache affinity stay server-side without resending history arrays,
   or `store=False` for stateless Zero Data Retention (ZDR).

Run `python low_infra_harness.py` to execute the Part B live demo comparing
stateful `previous_interaction_id` caching against `store=False` ZDR mode.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse


@dataclass
class ManagedTurnResult:
    interaction_id: str | None = None
    previous_interaction_id: str | None = None
    environment_id: str | None = None
    status: str | None = None
    output_text: str = ""
    tool_calls: list[str] = field(default_factory=list)
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None
    thought_tokens: int | None = None
    error: str | None = None

    @property
    def cache_hit_ratio(self) -> float:
        denom = self.input_tokens or self.total_tokens or 0
        if denom <= 0 or not self.cached_tokens:
            return 0.0
        return self.cached_tokens / denom


def _hydrate_from_interaction(result: ManagedTurnResult, interaction: Any) -> None:
    if getattr(interaction, "id", None):
        result.interaction_id = interaction.id
    if getattr(interaction, "previous_interaction_id", None):
        result.previous_interaction_id = interaction.previous_interaction_id
    if getattr(interaction, "environment_id", None):
        result.environment_id = interaction.environment_id
    if getattr(interaction, "status", None):
        result.status = interaction.status
    errors = getattr(interaction, "errors", None)
    if errors and not result.error:
        result.error = str(errors)
    usage = getattr(interaction, "usage", None)
    if usage is not None:
        if getattr(usage, "total_input_tokens", None) is not None:
            result.input_tokens = usage.total_input_tokens
        if getattr(usage, "total_output_tokens", None) is not None:
            result.output_tokens = usage.total_output_tokens
        if getattr(usage, "total_tokens", None) is not None:
            result.total_tokens = usage.total_tokens
        if getattr(usage, "total_cached_tokens", None) is not None:
            result.cached_tokens = usage.total_cached_tokens
        if getattr(usage, "total_thought_tokens", None) is not None:
            result.thought_tokens = usage.total_thought_tokens


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


def inspect_sandbox_environment(
    client: Any,
    environment_id: str,
    *,
    path: str = ".",
) -> dict[str, Any]:
    """Reads persistent remote sandbox metadata and workspace file snapshot."""
    env_meta = client.environments.get(id=environment_id)
    files_resp = client.environments.files.list(environment=environment_id, path=path)
    entries = getattr(files_resp, "files", None) or []
    return {
        "id": getattr(env_meta, "id", environment_id),
        "status": getattr(env_meta, "status", None),
        "file_count": getattr(env_meta, "file_count", None),
        "size_bytes": getattr(env_meta, "size_bytes", None),
        "files": [
            getattr(f, "path", None) or getattr(f, "name", str(f))
            for f in entries
        ],
    }


def run_managed_interaction(
    client: Any,
    *,
    agent: str,
    prompt: str,
    previous_interaction_id: str | None = None,
    environment: str | dict[str, Any] | None = None,
    tools: list[dict[str, Any]] | None = None,
    store: bool = True,
    background: bool = True,
    on_text_delta: Callable[[str], None] | None = None,
) -> ManagedTurnResult:
    """Streams a single turn against a Managed Agent and extracts state handles.

    Supports all three state patterns on a reusable Managed Agent (`agent`):
    1. First interaction: omit `previous_interaction_id` and `environment_id` to
       start a new conversation and provision a fresh 7-day Linux sandbox.
    2. Session follow-up: pass `previous_interaction_id` (chaining conversation
       `steps[]` & `thought` context) together with `environment_id` (reattaching
       to the persistent Linux sandbox where agent-built scripts reside).
    3. Fresh agent reuse: call the same `agent` ID again without
       `previous_interaction_id` or `environment_id` to serve a new user or task
       in a clean, isolated sandbox.
    """
    if not store and previous_interaction_id is not None:
        raise ValueError("previous_interaction_id requires store=True.")

    kwargs: dict[str, Any] = {
        "agent": agent,
        "input": prompt,
        "stream": True,
        "background": background,
        "store": store,
    }
    if previous_interaction_id is not None:
        kwargs["previous_interaction_id"] = previous_interaction_id
    if environment is not None:
        kwargs["environment"] = environment
    if tools is not None:
        kwargs["tools"] = tools

    result = ManagedTurnResult(previous_interaction_id=previous_interaction_id)
    chunks: list[str] = []

    stream = client.interactions.create(**kwargs)
    for event in stream:
        event_type = getattr(event, "event_type", "")

        interaction = getattr(event, "interaction", None)
        if interaction is not None:
            _hydrate_from_interaction(result, interaction)

        if event_type == "error":
            err = getattr(event, "error", None)
            result.error = str(err) if err is not None else "Unknown SSE interaction error"

        elif event_type == "step.start":
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

    # `InteractionSseEventInteraction` does not statically declare `environment_id`;
    # fetch the materialized Interaction resource if SSE frames omitted it.
    get_fn = getattr(getattr(client, "interactions", None), "get", None)
    if result.interaction_id and result.environment_id is None and callable(get_fn):
        full_interaction = get_fn(result.interaction_id)
        _hydrate_from_interaction(result, full_interaction)
        if not chunks and getattr(full_interaction, "output_text", None):
            chunks.append(full_interaction.output_text)

    result.output_text = "".join(chunks)

    if result.error or result.status in ("failed", "cancelled", "incomplete"):
        raise RuntimeError(
            f"Managed interaction {result.interaction_id!r} ended with "
            f"status={result.status!r}, error={result.error!r}"
        )

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
    """Executes one stateful or ZDR model turn over the Interactions API.

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


def _format_usage(interaction: Any) -> str:
    usage = getattr(interaction, "usage", None)
    if usage is None:
        return "usage=unavailable"
    inp = getattr(usage, "total_input_tokens", None) or 0
    cached = getattr(usage, "total_cached_tokens", None) or 0
    thought = getattr(usage, "total_thought_tokens", None) or 0
    total = getattr(usage, "total_tokens", None) or 0
    hit_pct = (100.0 * cached / inp) if inp > 0 else 0.0
    return (
        f"input={inp:,} | cached={cached:,} ({hit_pct:.1f}% hit) | "
        f"thought={thought:,} | total={total:,}"
    )


def run_part_b_demo(model: str = "gemini-3.8-flash") -> None:
    """Runs Part B live demo: stateful `previous_interaction_id` vs `store=False` ZDR."""
    from dotenv import load_dotenv
    from google import genai

    load_dotenv()
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        raise ValueError("GOOGLE_CLOUD_PROJECT is required to run the live Part B demo.")

    catalog_mcp_url = os.getenv("CATALOG_MCP_URL")
    tools = (
        [{"type": "mcp_server", "name": "knowledge-catalog-mcp", "url": catalog_mcp_url}]
        if catalog_mcp_url
        else None
    )
    system_instruction = (
        "You are a Tokenomics-governed analytics architect. Keep answers concise (<= 4 sentences) "
        "and enforce _TABLE_SUFFIX partition filters, dryRun<=100MB gates, and zero SELECT *."
    )

    client = genai.Client(enterprise=True, project=project_id, location="global")
    stored_ids: list[str] = []

    try:
        print(f"=== Part B.1: Stateful Chaining via previous_interaction_id (model={model}) ===")
        t1 = run_thin_model_turn(
            client,
            model=model,
            system_instruction=system_instruction,
            tools=tools,
            prompt=(
                "Explain why filtering `ga_sessions_*` by `_TABLE_SUFFIX` and running a `dryRun` "
                "byte check before query execution prevents runaway analytical scan costs."
            ),
            store=True,
        )
        stored_ids.append(t1.id)
        print(f"[Turn 1] id={t1.id} | {_format_usage(t1)}")
        print(t1.output_text)

        t2 = run_thin_model_turn(
            client,
            model=model,
            system_instruction=system_instruction,
            tools=tools,
            previous_interaction_id=t1.id,
            prompt=(
                "Now write the single parameterized BigQuery SQL query for top 3 `trafficSource.source` "
                "values on August 1, 2017 adhering to those exact rules."
            ),
            store=True,
        )
        stored_ids.append(t2.id)
        print(f"\n[Turn 2 — Chained] id={t2.id} (prev={t1.id}) | {_format_usage(t2)}")
        print(t2.output_text)

        print(f"\n=== Part B.2: Stateless Zero Data Retention Mode (store=False, model={model}) ===")
        zdr_turn = run_thin_model_turn(
            client,
            model=model,
            system_instruction=system_instruction,
            prompt="Confirm in 2 sentences how `store=False` satisfies strict Zero Data Retention.",
            store=False,
        )
        print(f"[ZDR Turn] id={getattr(zdr_turn, 'id', None)} | {_format_usage(zdr_turn)}")
        print(zdr_turn.output_text)
    finally:
        for interaction_id in stored_ids:
            try:
                client.interactions.delete(interaction_id)
            except Exception:  # noqa: BLE001, S110
                pass
        if stored_ids:
            print(f"\nCleaned up {len(stored_ids)} stored interaction(s).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run Lab 5 Part B thin `/interactions` harness live demo."
    )
    parser.add_argument(
        "--model",
        default=os.getenv("INTERACTIONS_MODEL", "gemini-3.8-flash"),
        help="Model identifier on Gemini Platform (default: gemini-3.8-flash).",
    )
    cli_args = parser.parse_args()
    run_part_b_demo(model=cli_args.model)
