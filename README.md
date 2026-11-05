# Agent Builder Labs — Gemini Platform, ADK, MCP, A2A & Managed Agents API

Hands-on lab progression demonstrating **enterprise data analytics agents** on Google Cloud's **Gemini Platform**—from semantic catalog grounding and code-first **ADK** multi-agent graphs to zero-container **Managed Agents API** and low-infrastructure **Interactions API** harnesses.

---

## Lab Sequence

| Lab | Directory | Architecture Focus | Infra Footprint |
| :---: | :--- | :--- | :--- |
| **1** | [`knowledgeCatalog-bigquery-sync/`](knowledgeCatalog-bigquery-sync/) | Seed `ga_sessions` partition rules and canonical metric definitions (`bounce-rate`, `monthly-active-users`) into **Dataplex Knowledge Catalog**. | Python setup script |
| **2** | [`create-knowledge-catalog-mcp-server/`](create-knowledge-catalog-mcp-server/) | Deploy a **FastMCP** server on Cloud Run exposing `search_knowledge_catalog`, `get_semantic_metric`, and `get_table_metadata` over SSE. | Cloud Run (`knowledge-catalog-mcp`) |
| **3** | [`judge-agent/`](judge-agent/) | Deploy a remote **A2A Judge Agent** (`gemini-4-argon`) scoring SQL partition hygiene, semantic alignment, groundedness, and preference adherence on a 4-point ordinal scale. | Cloud Run (`judge-agent`) |
| **4** | [`data_analytics_agent/`](data_analytics_agent/) | **Code-First Agent (`ADK`)**: Build a custom Python `LlmAgent` combining the Knowledge Catalog MCP circuit-breaker, `BigQueryToolset`, `Memory Bank`, and `RemoteA2aAgent` delegation. | Custom Python package / Agent Runtime container |
| **5** | [`managed-agents-api/`](managed-agents-api/) | **Config-First Managed Agents API & Low-Infra `/interactions` Harness**: Provision via `client.agents.create`, mount portable `SKILL.md`, and have the agent **build and execute its own Python BigQuery CLI (`/tmp/bq_runner.py`) inside `code_execution` with zero BigQuery MCP server** (authenticated via host egress proxy `network.allowlist[].transform` headers). | **Zero container build** (`google-genai >= 2.4.0`) |

---

## Choosing the Right Execution Tier (Lab 4 vs. Lab 5)

| Dimension | **Code-First ADK on Agent Runtime (`Lab 4`)** | **Thin `/interactions` Harness (`Lab 5` Part B)** | **Managed Agents API (`Lab 5` Part A)** |
| :--- | :--- | :--- | :--- |
| **Best Fit** | Bespoke multi-agent state machines, deterministic Python middleware (`before_tool_callback`), and custom A2A graphs | Lightweight custom loops needing direct control over model turns without running an agent framework | Fastest speed-to-market: desk/analyst and coding agents live in minutes via a single declarative API call |
| **Deployment Unit** | Container / Python app deployed to `Agent Runtime` or Cloud Run | ~50-line client script or existing microservice calling `POST .../interactions` | Declarative `Agent` control-plane resource (`client.agents.create`) + managed remote sandbox |
| **State & Prefix Cache** | `Agent Sessions` + Spanner `Memory Bank` | Server-chained `previous_interaction_id` (retains `thought_signature` automatically) or ZDR `store=False` | Managed `interaction.id` + reusable `environment_id` (`7-day` rolling TTL Linux sandbox) |
| **Skills & Tools** | Python `FunctionTool`, `BigQueryToolset`, `RemoteA2aAgent` | Turn-scoped `tools[]` (functions or remote `mcp_server`) | Mounted `SKILL.md` (`skill_registry`, `gcs`, or `inline`) + agent-built `code_execution` scripts with host proxy `transform` auth (plus optional remote `mcp_server`) |

---

## Quick Start

1. Set your active Google Cloud project and authenticate Application Default Credentials:
   ```bash
   gcloud config set project <YOUR_PROJECT_ID>
   gcloud auth application-default login
   ```
2. Run Labs **1 → 2** to stand up the Knowledge Catalog entries and the `knowledge-catalog-mcp` Cloud Run server.
3. Run **Lab 3** & **Lab 4** for the code-first **ADK + A2A** track, or jump straight from **Lab 2** into **Lab 5** ([`managed-agents-api/README.md`](managed-agents-api/README.md)) to provision and test the **Managed Agents API** and low-infrastructure `/interactions` harness.
