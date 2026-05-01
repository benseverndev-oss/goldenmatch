# GoldenCheck

## Tagline
Auto-discover validation rules from data — scan, profile, health-score. No rules to write.

## Description
GoldenCheck flips data validation on its head: instead of writing rules first, it scans your data and discovers what rules should exist. Point it at a CSV, Parquet, or Excel file and get a full quality report — nulls, duplicates, format violations, outliers, drift — with severity, confidence, and affected rows. Pin the rules you care about into a config and validate on every pipeline run. Ships with domain packs for healthcare, finance, ecommerce, and more. DQBench score: 88.40.

## Setup Requirements
No environment variables required. Works out of the box with local files.

## Category
Data & Analytics

## Use Cases
Data validation, Data profiling, Data quality monitoring, ETL pipeline checks, Schema drift detection, Compliance auditing, Data onboarding

## Features
- Zero-config data scanning — discovers validation rules automatically from any CSV, Parquet, or Excel file
- Health scoring (A-F, 0-100) for quick data quality assessment
- 14 built-in profiler checks: type inference, nullability, uniqueness, format detection, range, cardinality, pattern consistency, encoding, sequence, drift, temporal order, null correlation, cross-column validation
- Domain packs for healthcare, finance, ecommerce with specialized semantic type detection
- Pin discovered rules into goldencheck.yml for repeatable validation
- Column-level detail: type, null%, unique%, min/max, top values, detected formats
- Community domain pack marketplace — install custom domain packs
- Severity and confidence scoring on every finding
- MCP server with 9 tools for AI-assisted data quality workflows
- Integrates with GoldenFlow (fixes) and GoldenPipe (orchestration)

## Getting Started
- "Scan my sales data for quality issues"
- "What's the health score of customers.csv?"
- "Profile the orders table and show me column statistics"
- "List available domain packs for healthcare data"
- Tool: scan — Scan a data file for quality issues with severity, confidence, and affected rows
- Tool: profile — Get column-level statistics and an overall health score (A-F)
- Tool: health_score — Quick health grade (A-F, 0-100) for any data file
- Tool: validate — Validate data against pinned rules in goldencheck.yml
- Tool: get_column_detail — Deep-dive into a specific column's profile and findings
- Tool: list_domains — Browse available domain packs (healthcare, finance, etc.)
- Tool: install_domain — Download a community domain pack for specialized validation

## Tags
data-validation, data-quality, profiling, data-checks, health-score, csv, parquet, excel, domain-packs, drift-detection, etl, pipeline, zero-config, mcp, ai-tools

## Documentation URL
https://benzsevern.github.io/goldencheck/

## Health Check URL
https://goldencheck-mcp-production.up.railway.app/mcp/
