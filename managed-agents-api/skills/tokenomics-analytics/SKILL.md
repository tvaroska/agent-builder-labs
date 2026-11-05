---
name: tokenomics-analytics
description: Enforces Tokenomics-governed BigQuery analytics by consulting the Knowledge Catalog MCP server before scanning tables, answering metric and schema questions at zero scan cost, and mandating partition filters on live queries.
---

# Tokenomics-Governed BigQuery Analytics Skill

Follow this three-step sequence whenever answering analytics, metric formula, schema, or BigQuery data questions.

## 1. Mandatory Knowledge Catalog First (Zero-Scan Circuit Breaker)

- Always query the attached `knowledge-catalog-mcp` MCP tools (`search_knowledge_catalog`, `get_semantic_metric`, `get_table_metadata`) **before** writing or executing any BigQuery SQL.
- If the user asks a conceptual or metadata question (for example, *"What is our business formula for bounce rate?"*, *"What datasets are available?"*, or *"What are the partition rules for `ga_sessions`?"*):
  - Answer strictly from the Knowledge Catalog MCP results.
  - Do **not** invoke BigQuery jobs or `list_datasets` calls.

## 2. Partition-Safe BigQuery Execution (Live Row Queries Only)

Only execute a BigQuery query when the user asks for concrete row values or aggregated numbers that cannot be answered from catalog metadata alone:

1. Call `get_table_metadata` first to verify partition columns, sharding suffixes, and cost guardrails.
2. Never run `SELECT *` on analytical tables; project only the required columns.
3. For date-sharded tables such as `bigquery-public-data.google_analytics_sample.ga_sessions_*`:
   - Always filter on `_TABLE_SUFFIX` (for example, `_TABLE_SUFFIX = '20170801'` for a single day or `_TABLE_SUFFIX BETWEEN '20170725' AND '20170801'` for multi-day ranges).
   - Bill the query to the active `GOOGLE_CLOUD_PROJECT`.

## 3. Grounded Reporting

- Quote the exact `_TABLE_SUFFIX` filter and Knowledge Catalog metric definition used.
- Ensure every number in the final summary appears verbatim in the SQL query result.
