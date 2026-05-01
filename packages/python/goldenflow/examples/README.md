# GoldenFlow Examples

## Python

| Script | Description | Prerequisites |
|--------|-------------|---------------|
| `zero_config.py` | Zero-config transform on messy data -- auto-detect and fix | `goldenflow` |
| `configured_transform.py` | Explicit transforms via `GoldenFlowConfig` | `goldenflow` |
| `schema_mapping.py` | Map columns between source and target schemas | `goldenflow` |
| `benchmark.py` | Run DQBench Transform benchmark (score: 100.00) | `goldenflow`, `dqbench` |
| `transform_basic.py` | Zero-config file transform with manifest output | `goldenflow` |
| `config_based.py` | Transform with a YAML config for explicit control | `goldenflow` |
| `domain_pack.py` | Healthcare domain transforms for clinical data | `goldenflow` |

```bash
pip install goldenflow
python examples/zero_config.py
```

## TypeScript

| Script | Description | Prerequisites |
|--------|-------------|---------------|
| `typescript/zero-config.ts` | Zero-config transform -- auto-detect and fix | `goldenflow` (npm) |
| `typescript/configured-transform.ts` | Explicit transforms per column with dedup | `goldenflow` (npm) |
| `typescript/schema-mapping.ts` | Auto-map columns between source and target | `goldenflow` (npm) |
| `typescript/streaming.ts` | Stream-process large datasets in chunks | `goldenflow` (npm) |
| `typescript/profiling.ts` | Profile data + auto-generate config | `goldenflow` (npm) |
| `typescript/edge-safe.ts` | Edge-safe usage (browsers, Workers, Edge Runtime) | `goldenflow` (npm) |

```bash
npm install goldenflow
npx tsx examples/typescript/zero-config.ts
npx tsx examples/typescript/configured-transform.ts
npx tsx examples/typescript/schema-mapping.ts
npx tsx examples/typescript/streaming.ts
npx tsx examples/typescript/profiling.ts
npx tsx examples/typescript/edge-safe.ts
```
