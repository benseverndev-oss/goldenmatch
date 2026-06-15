# AbbrevAlign benchmark vs goldenmatch comparators

Abbreviation-heavy ER dataset: **298 records**, **44253 candidate pairs** (393 match / 43860 non-match). Comparators marked `*` are goldenmatch's actual rapidfuzz functions; MongeElkan/SoftTFIDF are the hybrids AbbrevAlign generalizes.

## In-sample ceiling (threshold chosen on all pairs — optimistic)

| Method | Best F1 | Precision | Recall | ROC-AUC | Threshold |
| --- | ---: | ---: | ---: | ---: | ---: |
| AbbrevAlign | **0.819** | 0.754 | 0.896 | 0.991 | 0.859 |
| TokenRoleAlign | **0.748** | 0.804 | 0.700 | 0.947 | 0.837 |
| MongeElkan | **0.741** | 0.825 | 0.672 | 0.942 | 0.835 |
| JW∪AbbrevAlign | **0.731** | 0.603 | 0.929 | 0.998 | 0.859 |
| RecurAlign | **0.634** | 0.596 | 0.677 | 0.941 | 0.691 |
| TokenSortRatio* | **0.598** | 0.604 | 0.593 | 0.862 | 0.696 |
| JaroWinkler* | **0.439** | 0.334 | 0.641 | 0.964 | 0.733 |
| Levenshtein* | **0.419** | 0.385 | 0.461 | 0.843 | 0.574 |
| PartialRatio* | **0.393** | 0.279 | 0.664 | 0.983 | 0.708 |
| SoftTFIDF | **0.378** | 0.527 | 0.295 | 0.867 | 0.550 |
| TokenJaccard* | **0.306** | 0.206 | 0.595 | 0.793 | 0.333 |

## Recall by positive slice (at each method's best-F1 threshold)

| Method | Abbrev/acronym | Nickname/initial | Typo |
| --- | ---: | ---: | ---: |
| AbbrevAlign | 0.871 | 0.890 | 1.000 |
| TokenRoleAlign | 0.381 | 0.865 | 1.000 |
| MongeElkan | 0.381 | 0.810 | 1.000 |
| JW∪AbbrevAlign | 0.959 | 0.890 | 1.000 |
| RecurAlign | 0.422 | 0.790 | 1.000 |
| TokenSortRatio* | 0.449 | 0.605 | 1.000 |
| JaroWinkler* | 0.667 | 0.625 | 1.000 |
| Levenshtein* | 0.361 | 0.505 | 1.000 |
| PartialRatio* | 0.449 | 0.805 | 1.000 |
| SoftTFIDF | 0.306 | 0.160 | 0.731 |
| TokenJaccard* | 0.224 | 0.800 | 0.808 |

## Held-out F1 (5-fold, entity-grouped CV — the honest number)

Thresholds (and the learned combiner) are fit on train entities and evaluated on unseen test entities, then pooled. This is the comparison that actually matters.

| Method | Held-out F1 | Precision | Recall | ROC-AUC |
| --- | ---: | ---: | ---: | ---: |
| StackLearned (JW+Abbrev+Nick) | **0.921** | 0.925 | 0.916 | 0.992 |
| AbbrevAlign | **0.899** | 0.905 | 0.893 | 0.990 |
| JW∪AbbrevAlign | **0.873** | 0.857 | 0.888 | 0.997 |
| TokenRoleAlign | **0.781** | 0.900 | 0.690 | 0.949 |
| MongeElkan | **0.775** | 0.886 | 0.690 | 0.943 |
| RecurAlign | **0.748** | 0.848 | 0.669 | 0.943 |
| TokenSortRatio* | **0.708** | 0.879 | 0.593 | 0.863 |
| PartialRatio* | **0.708** | 0.755 | 0.667 | 0.987 |
| JaroWinkler* | **0.697** | 0.740 | 0.659 | 0.967 |
| TokenJaccard* | **0.625** | 0.657 | 0.595 | 0.795 |
| Levenshtein* | **0.582** | 0.796 | 0.458 | 0.848 |
| SoftTFIDF | **0.491** | 0.688 | 0.382 | 0.870 |

## Findings

- **Best held-out F1: `StackLearned (JW+Abbrev+Nick)` (0.921).**
- **Learned combiner (JW + AbbrevAlign + NickGraph) beats the JaroWinkler baseline** on held-out F1 (0.921 vs 0.697, Δ +0.224) — the principled fusion, not naive `max()`. AbbrevAlign and NickGraph contribute as features.
- **AbbrevAlign (nickname- + stopword-acronym-aware) alone:** held-out F1 0.899, precision 0.905, AUC 0.990. Two iterations drove this: v2 folded in nickname equivalence (Bob=Robert), v3 made acronym matching skip stopwords (FBI<-Federal Bureau *of* Investigation). It beats JaroWinkler on F1 (Δ +0.202). Both precision and recall discriminate here — the larger set carries real hard negatives.
- **Recommendation:** ship the learned combiner (JW + AbbrevAlign + NickGraph), which wins held-out (Δ +0.224 vs JaroWinkler) — learned weights handle the acronym-collision precision tradeoff.

> 393 positives across 298 records with realistic noise and organic hard negatives — the v2/v3 gains generalize and the learned combiner now wins. Synthetic, so the transformation distribution is known; the honest next step is a real labeled set (Cora / DBLP-ACM / company names) to confirm.
