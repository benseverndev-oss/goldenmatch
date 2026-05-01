# GoldenPipe

## Tagline
One command to validate, transform, and deduplicate — chain GoldenCheck + Flow + Match.

## Description
GoldenPipe is the orchestrator for the Golden Suite. Chain data validation (GoldenCheck), transformation (GoldenFlow), and deduplication (GoldenMatch) into a single pipeline that runs with one command. Define stages in a YAML config or let GoldenPipe auto-discover installed stages. Validate pipeline wiring before execution, get natural-language explanations of what each stage does, and run the full pipeline on any data file. DQBench Pipeline score: 88.07.

## Setup Requirements
No environment variables required. Install the Golden Suite tools you want to chain: `pip install goldencheck goldenflow goldenmatch goldenpipe`.

## Category
Data & Analytics

## Use Cases
Data pipeline orchestration, End-to-end data quality, Automated data cleaning, Multi-step ETL, Data onboarding workflows, Quality-first pipelines

## Features
- One-command pipeline — chain validation, transformation, and deduplication
- Auto-discovers installed Golden Suite stages (GoldenCheck, GoldenFlow, GoldenMatch)
- YAML pipeline configuration with stage ordering and parameters
- Pipeline validation — check wiring before execution
- Natural-language pipeline explanations — understand what each stage does
- Pluggable stage architecture — add custom stages via entry points
- 4 MCP tools for AI-assisted pipeline orchestration
- DQBench Pipeline score: 88.07

## Getting Started
- "Run the full data quality pipeline on my customer file"
- "What stages are available in my pipeline?"
- "Explain what this pipeline config does"
- "Validate my pipeline configuration before running it"
- Tool: run_pipeline — Run a complete pipeline on a data file
- Tool: list_stages — List all discovered pipeline stages
- Tool: explain_pipeline — Get a natural-language explanation of a pipeline config
- Tool: validate_pipeline — Check pipeline wiring and stage compatibility

## Tags
pipeline, orchestration, data-quality, etl, data-validation, data-transformation, deduplication, golden-suite, yaml, zero-config, mcp, ai-tools

## Documentation URL
https://benzsevern.github.io/goldenpipe/

## Health Check URL
https://goldenpipe-mcp-production.up.railway.app/mcp/
