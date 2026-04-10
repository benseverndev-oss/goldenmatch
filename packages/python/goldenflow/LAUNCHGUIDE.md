# GoldenFlow

## Tagline
Standardize, reshape, and normalize messy data — CSV, Excel, Parquet, S3, databases.

## Description
GoldenFlow is a data transformation toolkit that standardizes, reshapes, and normalizes messy data before it hits your pipeline. Works on files (CSV, Excel, Parquet), cloud storage (S3, GCS), or live databases. Zero-config mode auto-detects what needs fixing. Bridges GoldenCheck findings to automatic fixes — scan with GoldenCheck, fix with GoldenFlow. Includes schema mapping, diff comparison, and a library of pluggable transforms. DQBench Transform score: 100/100.

## Setup Requirements
No environment variables required. Works out of the box with local files. For S3/GCS, configure standard AWS/GCP credentials.

## Category
Data & Analytics

## Use Cases
Data cleaning, Schema standardization, ETL preprocessing, Data normalization, Format conversion, Data migration, Column mapping

## Features
- Zero-config transformation — auto-detects and fixes common data issues
- Schema mapping between source and target files
- Diff comparison — see exactly what changed between two data files
- Config learning — generates YAML configs from data patterns automatically
- Pluggable transform library with dry-run validation
- Domain packs: people/HR, ecommerce, finance, healthcare, real estate
- Bridge from GoldenCheck: maps findings directly to recommended transforms
- Works with CSV, Excel, Parquet, S3, GCS, and databases
- 10 MCP tools for AI-assisted data transformation workflows
- DQBench Transform score: 100/100

## Getting Started
- "Clean up and standardize my customer data"
- "What transforms would fix the issues GoldenCheck found?"
- "Show me the diff between the original and cleaned file"
- "Learn a transform config from my sales data patterns"
- Tool: transform — Transform a data file with zero-config or config-driven rules
- Tool: map — Auto-map schemas between source and target files
- Tool: profile — Profile a data file showing column types, nulls, and patterns
- Tool: learn — Generate a YAML config from detected data patterns
- Tool: diff — Compare two data files and show what changed
- Tool: validate — Dry-run a transform to preview changes without writing output
- Tool: select_from_findings — Map GoldenCheck findings to recommended transforms

## Tags
data-transformation, data-cleaning, etl, normalization, standardization, schema-mapping, csv, parquet, excel, s3, gcs, zero-config, mcp, ai-tools, data-quality

## Documentation URL
https://benzsevern.github.io/goldenflow/

## Health Check URL
https://goldenflow-mcp-production.up.railway.app/mcp/
