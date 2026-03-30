# GoldenPipe Examples

## Quick Start

```bash
pip install goldenpipe[golden-suite]
```

## Examples

| Script | What It Does | Prerequisites |
|--------|-------------|--------------|
| `full_suite_demo.py` | Each Golden Suite tool individually, then the full pipeline | `goldenpipe[golden-suite]` |
| `benchmark_suite.py` | DQBench scores for all 4 tools with visual scorecard | `goldenpipe[golden-suite] dqbench` |
| `basic_pipeline.py` | Run a full pipeline on a CSV file | `goldenpipe[golden-suite]` |
| `selective_stages.py` | Run only check + flow, skip match | `goldenpipe[golden-suite]` |
| `custom_pipeline.py` | Build a custom pipeline with specific stages | `goldenpipe[golden-suite]` |

## GitHub Actions

Run from the **Actions** tab → **Try GoldenPipe** → **Run workflow**:

| Mode | What It Does | Time |
|------|-------------|------|
| Full Suite Demo | Each tool individually + pipeline | ~30s |
| Pipeline Only | GoldenPipe on sample data | ~10s |
| DQBench Benchmark | Full benchmark scorecard (4 categories) | ~2min |

## DQBench Scores

Run `python examples/benchmark_suite.py` to reproduce:

| Tool | Category | Score |
|------|----------|-------|
| GoldenCheck | Detect | 88.40 |
| GoldenFlow | Transform | 100.00 |
| GoldenMatch | ER | 95.30 (with LLM) / 77.21 |
| GoldenPipe | Pipeline | 88.07 |

Set `OPENAI_API_KEY` for LLM-enhanced ER scoring (~$0.25 per run).
