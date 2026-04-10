# InferMap

## Tagline
Map messy columns to a known schema — 7 scorers, domain dictionaries, F1 0.84. Zero config.

## Description
InferMap is a schema mapping engine. Give it any two field collections — CSVs, DataFrames, database tables, in-memory records — and it figures out which source field corresponds to which target field, with confidence scores and human-readable reasoning. Runs 7 weighted scorers (exact match, aliases, initialisms, semantic types, statistical profiles, fuzzy names) through the Hungarian algorithm for optimal 1:1 assignment. Ships with domain dictionaries for healthcare, finance, and ecommerce. Confidence calibration transforms raw scores into calibrated probabilities. Available in Python (PyPI) and TypeScript (npm) with cross-language parity. F1 0.84 on 162 real-world cases.

## Setup Requirements
No environment variables required. Works out of the box with local files.

## Category
Data & Analytics

## Use Cases
Schema mapping, Column matching, Data integration, ETL field mapping, Schema migration, Data onboarding, Cross-system data linking

## Features
- Zero-config schema mapping — auto-detects field correspondences with confidence scores
- 7 built-in scorers: exact match, alias, initialism, pattern type, profile, fuzzy name, LLM (pluggable)
- Hungarian algorithm for globally optimal 1:1 field assignment
- Domain dictionaries for healthcare, finance, and ecommerce (curated alias sets)
- Confidence calibration — Isotonic and Platt calibrators (ECE 0.46 to 0.005)
- Common-prefix canonicalization — strips schema-wide prefixes before matching
- Human-readable reasoning for every mapping decision
- Score matrix inspection for runner-up analysis and override UIs
- Save and reload mappings as YAML/JSON configs
- Apply mappings to rename DataFrame columns
- F1 0.84 on 162 real-world schema-matching cases
- Available in both Python and TypeScript with cross-language parity

## Getting Started
- "Map the columns in my CRM export to our canonical customer schema"
- "What fields in this CSV match our warehouse table?"
- "Inspect the schema of my data file"
- "Apply the saved mapping to rename columns in my export"
- Tool: map — Map source columns to target schema using weighted scorer pipeline
- Tool: inspect — Show fields, types, samples, and statistics for a schema
- Tool: validate — Check that a source file satisfies a mapping config
- Tool: apply — Apply a saved mapping config to remap a file's columns

## Tags
schema-mapping, column-matching, data-integration, etl, field-mapping, hungarian-algorithm, fuzzy-matching, data-onboarding, csv, parquet, zero-config, mcp, ai-tools, python, typescript

## Documentation URL
https://benzsevern.github.io/infermap/
