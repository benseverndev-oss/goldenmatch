# AbbrevAlign benchmark vs goldenmatch comparators

Abbreviation-heavy ER dataset: **50 records**, **1225 candidate pairs** (45 match / 1180 non-match). Comparators marked `*` are goldenmatch's actual rapidfuzz functions; MongeElkan/SoftTFIDF are the hybrids AbbrevAlign generalizes.

## In-sample ceiling (threshold chosen on all pairs — optimistic)

| Method | Best F1 | Precision | Recall | ROC-AUC | Threshold |
| --- | ---: | ---: | ---: | ---: | ---: |
| AbbrevAlign | **0.860** | 0.902 | 0.822 | 0.934 | 0.909 |
| JW∪AbbrevAlign | **0.851** | 0.881 | 0.822 | 0.985 | 0.909 |
| JaroWinkler* | **0.779** | 0.740 | 0.822 | 0.979 | 0.692 |
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
| AbbrevAlign | 0.815 | 0.800 | 1.000 |
| JW∪AbbrevAlign | 0.815 | 0.800 | 1.000 |
| JaroWinkler* | 0.741 | 0.933 | 1.000 |
| PartialRatio* | 0.519 | 0.800 | 1.000 |
| Levenshtein* | 0.519 | 1.000 | 1.000 |
| SoftTFIDF | 0.593 | 0.867 | 1.000 |
| TokenSortRatio* | 0.556 | 0.867 | 1.000 |
| RecurAlign | 0.481 | 0.667 | 1.000 |
| MongeElkan | 0.519 | 0.667 | 1.000 |
| TokenRoleAlign | 0.519 | 0.600 | 1.000 |
| TokenJaccard* | 0.519 | 0.867 | 0.667 |

## Held-out F1 (5-fold, entity-grouped CV — the honest number)

Thresholds (and the learned combiner) are fit on train entities and evaluated on unseen test entities, then pooled. This is the comparison that actually matters.

| Method | Held-out F1 | Precision | Recall | ROC-AUC |
| --- | ---: | ---: | ---: | ---: |
| AbbrevAlign | **0.902** | 1.000 | 0.822 | 0.933 |
| JaroWinkler* | **0.889** | 1.000 | 0.800 | 0.986 |
| JW∪AbbrevAlign | **0.875** | 1.000 | 0.778 | 0.987 |
| PartialRatio* | **0.831** | 1.000 | 0.711 | 0.980 |
| Levenshtein* | **0.816** | 1.000 | 0.689 | 0.852 |
| StackLearned (JW+Abbrev+Nick) | **0.800** | 1.000 | 0.667 | 0.988 |
| TokenSortRatio* | **0.784** | 1.000 | 0.644 | 0.818 |
| SoftTFIDF | **0.784** | 1.000 | 0.644 | 0.854 |
| TokenJaccard* | **0.767** | 1.000 | 0.622 | 0.820 |
| MongeElkan | **0.732** | 1.000 | 0.578 | 0.874 |
| RecurAlign | **0.732** | 1.000 | 0.578 | 0.907 |
| TokenRoleAlign | **0.732** | 1.000 | 0.578 | 0.912 |

## Findings

- **Best held-out F1: `AbbrevAlign` (0.902).**
- **Learned combiner (JW + AbbrevAlign + NickGraph) trails the JaroWinkler baseline** on held-out F1 (0.800 vs 0.889, Δ -0.089) — the principled fusion, not naive `max()`. AbbrevAlign and NickGraph contribute as features.
- **AbbrevAlign v2 (nickname-aware) alone:** held-out F1 0.902, precision 1.000, AUC 0.933 — folding in nickname equivalence (the v1→v2 iteration) lifts it beyond pure acronyms, and on held-out folds it now edges JaroWinkler on F1 (Δ +0.014). Recall is the discriminator here — held-out folds contain few cross-entity hard negatives, so precision saturates near 1.0.
- **Recommendation:** at this corpus size, use AbbrevAlign v2 directly as an added comparator (it wins held-out); the 6-feature logistic combiner overfits ~36 train positives per fold and underperforms — it is the right path only with more labels.

> Caveat: small curated corpus (45 positives), tiny folds — directional signal, not a production F1; treat deltas as suggestive. The learned combiner is data-starved here. Next: Cora / DBLP-ACM / a company-name set at scale with proper train/test volume.
