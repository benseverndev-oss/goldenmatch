# goldenmatch-kg

Drop-in goldenmatch entity resolution for knowledge-graph frameworks. Use goldenmatch as the ER stage inside neo4j-graphrag pipelines, LlamaIndex PropertyGraphIndex transforms, and Graphiti post-ingestion passes. goldenmatch handles zero-config deduplication; these adapters wire its output into each framework's seam.

## Install

```bash
pip install goldenmatch-kg                        # core only
pip install "goldenmatch-kg[neo4j-graphrag]"      # + neo4j-graphrag adapter
pip install "goldenmatch-kg[llamaindex]"           # + LlamaIndex adapter
pip install "goldenmatch-kg[graphiti]"             # + Graphiti adapter
```

## Benchmark lift (placeholder -- filled in Task 7 from committed RESULTS.md)

| Framework      | Default ER F1 | goldenmatch F1 | Lift     |
| -------------- | ------------- | -------------- | -------- |
| neo4j-graphrag | TBD           | TBD            | TBD      |
| LlamaIndex     | TBD           | TBD            | TBD      |
| Graphiti       | TBD           | TBD            | TBD      |

Numbers cited from `benchmarks/er-kg-bench/results/RESULTS.md` and `RESULTS_ghsuite.md` once the bench runs green.

## License

MIT
