# GoldenFlow Examples

| Script | Description | Prerequisites |
|--------|-------------|---------------|
| `zero_config.py` | Zero-config transform on messy data -- auto-detect and fix | `goldenflow` |
| `configured_transform.py` | Explicit transforms via `GoldenFlowConfig` | `goldenflow` |
| `schema_mapping.py` | Map columns between source and target schemas | `goldenflow` |
| `benchmark.py` | Run DQBench Transform benchmark (score: 100.00) | `goldenflow`, `dqbench` |
| `transform_basic.py` | Zero-config file transform with manifest output | `goldenflow` |
| `config_based.py` | Transform with a YAML config for explicit control | `goldenflow` |
| `domain_pack.py` | Healthcare domain transforms for clinical data | `goldenflow` |

## Quick Start

```bash
pip install goldenflow

# Zero-config -- just point at messy data
python examples/zero_config.py

# Configured -- explicit control per column
python examples/configured_transform.py

# Schema mapping -- match columns across schemas
python examples/schema_mapping.py

# DQBench benchmark (requires dqbench)
pip install dqbench
python examples/benchmark.py
```
