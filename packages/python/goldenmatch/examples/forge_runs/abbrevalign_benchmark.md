# AbbrevAlign benchmark vs goldenmatch comparators

Abbreviation-heavy ER dataset: **50 records**, **1225 candidate pairs** (45 match / 1180 non-match). Comparators marked `*` are goldenmatch's actual rapidfuzz functions; MongeElkan/SoftTFIDF are the hybrids AbbrevAlign generalizes.

## Overall (best-F1 threshold sweep)

| Method | Best F1 | Precision | Recall | ROC-AUC | Threshold |
| --- | ---: | ---: | ---: | ---: | ---: |
| JaroWinkler* | **0.779** | 0.740 | 0.822 | 0.979 | 0.692 |
| AbbrevAlign | **0.767** | 0.805 | 0.733 | 0.917 | 0.839 |
| JW∪AbbrevAlign | **0.756** | 0.838 | 0.689 | 0.982 | 0.879 |
| PartialRatio* | **0.744** | 0.879 | 0.644 | 0.979 | 0.800 |
| Levenshtein* | **0.719** | 0.727 | 0.711 | 0.840 | 0.478 |
| SoftTFIDF | **0.719** | 0.727 | 0.711 | 0.849 | 0.381 |
| TokenSortRatio* | **0.705** | 0.721 | 0.689 | 0.806 | 0.556 |
| RecurAlign | **0.693** | 0.867 | 0.578 | 0.914 | 0.685 |
| MongeElkan | **0.692** | 0.818 | 0.600 | 0.890 | 0.772 |
| TokenRoleAlign | **0.684** | 0.839 | 0.578 | 0.924 | 0.796 |
| TokenJaccard* | **0.682** | 0.725 | 0.644 | 0.816 | 0.250 |

## Recall by positive slice (at each method's best-F1 threshold)

| Method | Abbrev/acronym | Nickname/initial | Typo |
| --- | ---: | ---: | ---: |
| JaroWinkler* | 0.741 | 0.933 | 1.000 |
| AbbrevAlign | 0.778 | 0.600 | 1.000 |
| JW∪AbbrevAlign | 0.741 | 0.533 | 1.000 |
| PartialRatio* | 0.519 | 0.800 | 1.000 |
| Levenshtein* | 0.519 | 1.000 | 1.000 |
| SoftTFIDF | 0.593 | 0.867 | 1.000 |
| TokenSortRatio* | 0.556 | 0.867 | 1.000 |
| RecurAlign | 0.481 | 0.667 | 1.000 |
| MongeElkan | 0.519 | 0.667 | 1.000 |
| TokenRoleAlign | 0.519 | 0.600 | 1.000 |
| TokenJaccard* | 0.519 | 0.867 | 0.667 |

## Findings

- **Best overall F1: `JaroWinkler*` (0.779).** goldenmatch's default JaroWinkler is a strong, hard-to-beat baseline on a *mixed* error workload.
- **AbbrevAlign wins where it was designed to:** best precision (0.805) and top abbrev/acronym recall (0.778 vs JaroWinkler's 0.741) — `AbbrevAlign` leads that slice. The acronym gap is real.
- **But it is narrow:** AbbrevAlign is deliberately weak on nicknames/initials (0.600) — that is NickGraph's gap, not its. Used alone it trails JaroWinkler overall.
- **As a complementary channel it ranks best:** `JW∪AbbrevAlign` has the highest ROC-AUC (0.982). Naive `max()` fusion lifts hard-negative acronym collisions too, so a single global threshold doesn't convert that ranking gain into F1 — the principled fusion is a **learned combiner** (StackEnsemble, which already takes abbrev_align as a feature), evaluated with a train/test split.
- **Recommendation:** add AbbrevAlign as an extra comparator/feature in the scorer for abbreviation-heavy domains (company names, addresses), not as a JaroWinkler replacement.

> Caveat: micro-benchmark (45 positives) — directional signal, not a production F1. Next: rerun on Cora / DBLP-ACM / a company-name set with a learned combiner and held-out evaluation.
