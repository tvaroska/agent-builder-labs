---
name: tokenomics-analytics
description: Enforces Tokenomics-governed BigQuery analytics by consulting Knowledge Catalog first, answering metric and schema questions at zero scan cost, and building a partition-enforcing Python BigQuery CLI inside code_execution with zero BigQuery MCP dependency.
---

# Tokenomics-Governed BigQuery Analytics Skill

Follow this three-step sequence whenever answering analytics, metric formula, schema, or BigQuery data questions.

## 1. Mandatory Knowledge Catalog First (Zero-Scan Circuit Breaker)

- Whenever `knowledge-catalog-mcp` tools (`search_knowledge_catalog`, `get_semantic_metric`, `get_table_metadata`) are attached, consult them **before** touching BigQuery.
- For conceptual or metadata questions (such as *"What is our business formula for bounce rate?"*, *"What datasets are available?"*, or *"What are the partition rules for `ga_sessions`?"*):
  - Answer strictly from Knowledge Catalog results.
  - Do **not** run any BigQuery jobs or table scans.

<!-- PROTECTED:START -->
## 2. Non-Negotiable Sandbox & SQL Governance Guardrails (Immutable)

When live row values or aggregations are required, build and execute `/tmp/bq_runner.py` (and mirror a copy to `./bq_runner.py` in the workspace root) inside `code_execution` using Python's standard library (`urllib.request`, `json`, `os`, `re`, `shutil`, `sys`):

1. **Zero Ambient Secrets in Code:**
   - Read the billing project from `os.environ["GOOGLE_CLOUD_PROJECT"]`.
   - Send HTTPS `POST` requests directly to `https://bigquery.googleapis.com/bigquery/v2/projects/{project_id}/queries` (`Content-Type: application/json`).
   - Do **not** look for `gcloud`, ADC files, or OAuth tokens inside the sandbox—the host egress proxy automatically injects `Authorization` and `X-Goog-User-Project` headers on outbound requests to `bigquery.googleapis.com`.
2. **Local Pre-Flight SQL Guardrails (Before Any Network Call):**
   - Reject any query matching `SELECT\s+\*` (case-insensitive).
   - If the query references `ga_sessions`, reject it unless `_TABLE_SUFFIX` is present in the SQL text.
3. **Mandatory Dry-Run Byte Gate (`dryRun: true`):**
   - First `POST` `{"query": sql, "useLegacySql": false, "dryRun": true}`.
   - Parse `totalBytesProcessed` from the JSON response and abort if `int(totalBytesProcessed) > 100_000_000` (`100 MB`).
4. **Partition-Safe Execution (`dryRun: false`):**
   - `POST` `{"query": sql, "useLegacySql": false, "maximumBytesBilled": "100000000"}` (for `bigquery-public-data.google_analytics_sample.ga_sessions_*`, filter `_TABLE_SUFFIX = '20170801'` for single-day or `_TABLE_SUFFIX BETWEEN ...` for date ranges).
   - Zip `schema.fields` names with each row's `f[].v` values and print structured JSON including `dry_run_bytes` and `rows`.
<!-- PROTECTED:END -->

## 3. Grounded Reporting

- Cite the `_TABLE_SUFFIX` partition filter, `dry_run_bytes` processed, and Knowledge Catalog definition used.
- Every numerical figure in the final response must match the `/tmp/bq_runner.py` output verbatim.
