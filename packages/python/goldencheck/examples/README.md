# GoldenCheck Examples

## Quick Start

```bash
pip install goldencheck
```

## Examples

| Script | What It Does | Prerequisites |
|--------|-------------|--------------|
| `scan_and_profile.py` | Scan a CSV for issues, get health score | `goldencheck` |
| `scan_basic.py` | Scan a CSV file and print all findings | `goldencheck` |
| `validate_rules.py` | Validate data against a `goldencheck.yml` config | `goldencheck` |
| `domain_packs.py` | List and use industry-specific domain packs | `goldencheck` |
| `domain_pack.py` | Scan with the healthcare domain pack for clinical data types | `goldencheck` |
| `benchmark.py` | Run DQBench Detect benchmark | `goldencheck dqbench` |

## GitHub Actions

Run from **Actions** tab → **Try GoldenCheck** → **Run workflow**.

## DQBench Score

| Category | Score |
|----------|-------|
| Detect | **88.40** / 100 |
