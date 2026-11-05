# Lab 5: Zero-Infra Managed Agents API & Low-Infra `/interactions` Harness

In **Lab 4** ([`../data_analytics_agent/`](../data_analytics_agent/)), we built a code-first **ADK Worker Agent** requiring custom Python agent classes, `BigQueryToolset` wrappers, session services, and container deployment.

In **Lab 5**, we solve the **same Tokenomics-governed analytics problem on the Qwiklabs dataset (`ga_sessions_*`) with zero container build and zero BigQuery MCP server** using two low-infrastructure primitives on Google Cloud's **Gemini Platform** (`google-genai >= 2.4.0`):

1. **Part A — Config-First Managed Agents API (`client.agents` + `client.interactions` + `client.environments`):**
   - Provision a stateful custom agent on the control plane (`POST /v1beta1/projects/{P}/locations/global/agents`) in a single declarative call (with optional `agent_config` model & `max_total_tokens` cap).
   - Mount a portable [`skills/tokenomics-analytics/SKILL.md`](skills/tokenomics-analytics/SKILL.md) package (from **Skill Registry**, **Cloud Storage**, or **inline**) containing an immutable `<!-- PROTECTED:START -->` compliance block.
   - Attach **Lab 2's `knowledge-catalog-mcp`** strictly for semantic catalog discovery, while the Managed Agent **writes, unit-tests, and runs its own Python BigQuery CLI (`/tmp/bq_runner.py` & `./bq_runner.py`) inside `code_execution` with zero BigQuery MCP dependency**.
   - Authenticate outbound sandbox calls to `bigquery.googleapis.com` via **host egress proxy header injection (`network.allowlist[].transform`)** so **zero OAuth tokens or credentials ever enter sandbox memory, disk, or env vars**.
   - Reuse and inspect the returned **`environment_id`** (`7-day` rolling TTL Linux sandbox via `client.environments`) across turns, and pass `tools=[{"type": "code_execution"}]` on Turn 3 to prove BigQuery runs purely via the agent-built script.
2. **Part B — Thin Stateful Model Harness (`low_infra_harness.py`):**
   - For teams wanting a lightweight custom loop without adopting a full agent framework, `python low_infra_harness.py` drives `gemini-3.8-flash` / `gemini-4-argon` directly over `POST .../interactions`—comparing **`previous_interaction_id`** (retaining encrypted `thought` signatures and warm replica KV-cache affinity server-side) against **`store=False`** for 100% stateless Zero Data Retention (ZDR).

---

## Architecture

```mermaid
flowchart LR
    DEV["Developer / CI-CD\n(google-genai >= 2.4.0)"] -->|"1. Control Plane:\nclient.agents.create()"| AGENT["Managed Agent Resource\n(locations/global/agents/{id})"]
    DEV -->|"2. Data Plane:\nclient.interactions.create(env_id)"| INT["Interactions API\n(SSE Stream + 7d Sandbox)"]

    subgraph SANDBOX["Managed Remote Sandbox (environment_id — Zero Ambient Secrets)"]
        SKILL["Mounted SKILL.md\n(PROTECTED Guardrails)"]
        CLI["Agent-Built bq_runner.py\n(SQL Guard + dryRun<=100MB)"]
    end

    PROXY["Host Egress Proxy\n(allowlist transform:\nBearer + X-Goog-User-Project)"]

    INT --> SANDBOX
    INT -->|"Turns 1-2: Remote MCP (SSE)"| MCP["Lab 2: knowledge-catalog-mcp\n(Dataplex Semantic Definitions)"]
    CLI -->|"Turn 3: Plain HTTPS\n(Zero MCP)"| PROXY -->|"Authenticated REST"| BQ["BigQuery\n(ga_sessions_20170801)"]
```

### Model Interactions vs. Managed Agent Interactions

| Dimension | **Thin Model Interactions (`Part B`)** | **Managed Agent Interactions (`Part A`)** |
| :--- | :--- | :--- |
| **Target Parameter** | `model="gemini-3.8-flash"` (or `"gemini-4-argon"`) | `agent="<MANAGED_AGENT_ID>"` |
| **Multi-Turn Continuity** | **Immutable Turn Chain:** Each turn mints a new `interaction.id` linked via `previous_interaction_id` (`system_instruction` and `tools` are turn-scoped). | **Durable Task & Persistent Sandbox:** Backed by a server-managed task + `environment_id` (`7-day` rolling TTL Linux workspace inspectable via `client.environments`). |
| **Zero Data Retention (ZDR)** | Pass `store=False` (stateless `steps[]` response; incompatible with `previous_interaction_id`). | Zero ambient credentials in sandbox; delete stored interactions/environments on close. |

---

## Step 1: Prerequisites & Setup

Ensure **Lab 1** (`knowledgeCatalog-bigquery-sync`) and **Lab 2** (`create-knowledge-catalog-mcp-server`) have been deployed in your Qwiklabs / Google Cloud project, then run:

```bash
cd managed-agents-api
chmod +x instructions.sh
./instructions.sh
```

`instructions.sh` enables required APIs, grants `roles/aiplatform.serviceAgent` to the platform service identity, stages [`skills/tokenomics-analytics/SKILL.md`](skills/tokenomics-analytics/SKILL.md) to `gs://<PROJECT_ID>-managed-agent-skills`, and installs `google-genai>=2.4.0`.

*(Want zero Cloud Storage setup? Leave `AGENT_SKILL_GCS_BUCKET` unset—`create_managed_agent.py` automatically mounts `SKILL.md` inline inside `base_environment.sources`.)*

---

## Step 2: Run Offline Contract Tests (`<1s`, No Cloud Calls Required)

Verify the `SKILL.md` `PROTECTED` compliance block, `agent_config` validation, proxy header `transform` isolation (confirming tokens never enter `environment.env`), SSE `environment_id` fallback hydration, and `store=False` ZDR guards locally:

```bash
pytest test_managed_agent.py
```

---

## Step 3: Provision & Inspect the Custom Managed Agent (Part A)

```bash
export GOOGLE_CLOUD_PROJECT="$(gcloud config get-value project)"
export CATALOG_MCP_URL="$(gcloud run services describe knowledge-catalog-mcp --region=us-central1 --format='value(status.url)')/sse"

python create_managed_agent.py
```

Copy the printed `export MANAGED_AGENT_ID="..."` line into your shell.

> [!NOTE]
> **Updating an Existing Managed Agent:** `client.agents.update()` is not yet exposed in the Python SDK; to mutate an existing agent ID in place without recreating it, send a REST `PATCH https://aiplatform.googleapis.com/v1beta1/projects/${GOOGLE_CLOUD_PROJECT}/locations/global/agents/${MANAGED_AGENT_ID}?updateMask=systemInstruction,tools,baseEnvironment`.

---

## Step 4: Execute Multi-Turn Self-Built BigQuery Tool Verification (Part A)

```bash
python test_managed_agent.py
```

This runs three live turns against your Managed Agent using the exact Qwiklabs dataset from Labs 1–2:
1. **Turn 1 (Zero-Scan Metric Lookup):** Resolves the official `bounce_rate` SQL formula via `knowledge-catalog-mcp` with **zero BigQuery scans** and captures `environment_id`.
2. **Turn 2 (Build & Self-Test `bq_runner.py` in Persistent Sandbox):** Reattaches to `t1.environment_id`, fetches `ga_sessions` `_TABLE_SUFFIX` partition rules from the catalog, writes `/tmp/bq_runner.py` (mirrored to `./bq_runner.py`) in Python stdlib `urllib.request`, runs local unit assertions blocking `SELECT *` and unpartitioned queries, and inspects the remote workspace snapshot via `client.environments.files.list()`.
3. **Turn 3 (Pure `code_execution` BigQuery Run — Zero MCP Attached):** Overrides `tools=[{"type": "code_execution"}]` (detaching all MCP servers) and attaches short-lived proxy header injection (`network.allowlist[].transform`) for `bigquery.googleapis.com`. The agent runs `/tmp/bq_runner.py` from its persistent sandbox, verifies `dryRun` bytes (`<= 100 MB`), queries `bigquery-public-data.google_analytics_sample.ga_sessions_*` (`_TABLE_SUFFIX = '20170801'`), and reports exact visit counts and dry-run bytes.

---

## Step 5: Run Thin Stateful `/interactions` vs. ZDR Model Harness (Part B)

Compare multi-turn `previous_interaction_id` prefix-cache & `thought` lineage against `store=False` Zero Data Retention directly on `gemini-3.8-flash` (with zero agent framework or container):

```bash
python low_infra_harness.py
```

---

## Step 6: Clean Up

Managed Agent definitions and `7-day` remote sandboxes can be deleted explicitly when finished:

```bash
python cleanup_managed_agent.py --agent-id "$MANAGED_AGENT_ID"
```
