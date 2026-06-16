# Collective-ER Headline Benchmark

**n_entities**: 40  |  **seeds**: [7, 8, 9]  |  **total wall**: 7.1s

Collective config: `run_graph_er(propagation_mode="relational", alpha=0.65, rel_threshold=0.5)`

## Per-seed results

| seed | config      |    P  |    R  |   F1  | lift vs indep |
|-----:|:------------|------:|------:|------:|--------------:|
| 7    | independent | 0.585 | 0.813 | 0.681 |               |
| 7    | flat-boost  | 0.031 | 0.777 | 0.059 | -0.622       |
| 7    | collective  | 0.852 | 0.827 | 0.839 | +0.159       |
| 8    | independent | 0.595 | 0.820 | 0.690 |               |
| 8    | flat-boost  | 0.026 | 0.811 | 0.050 | -0.639       |
| 8    | collective  | 0.870 | 0.877 | 0.873 | +0.184       |
| 9    | independent | 0.547 | 0.743 | 0.630 |               |
| 9    | flat-boost  | 0.027 | 0.853 | 0.052 | -0.578       |
| 9    | collective  | 1.000 | 0.807 | 0.893 | +0.263       |

## Averages across seeds

| config      |    P  |    R  |   F1  |
|:------------|------:|------:|------:|
| independent | 0.576 | 0.792 | 0.667 |
| flat-boost  | 0.028 | 0.814 | 0.054 |
| collective  | 0.907 | 0.837 | 0.869 |

## Takeaway

Collective ER (relational propagation) averages **F1=0.869** vs independent F1=0.667 and flat-boost F1=0.054. Lift over attribute-only baseline: **+0.202**.
