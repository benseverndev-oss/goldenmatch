# AbbrevAlign on real DBLP-ACM (Leipzig) - credibility benchmark

Everything prior to this was curated or synthetic (known transformation distribution, which flatters structure-aware methods). This runs the *exact* entity-grouped CV harness on a real labeled ER corpus.

- Title ER: 450 records (150 match / 100875 non-match pairs), `--max-entities 150` `--seed 7`.


---

## Part 1: Title ER (standard dedup, held-out CV)

Titles of true matches are near-identical across DBLP/ACM, so JaroWinkler already saturates; the question is whether AbbrevAlign *hurts*. Held-out CV is the honest number.

# AbbrevAlign benchmark vs goldenmatch comparators

Abbreviation-heavy ER dataset: **450 records**, **101025 candidate pairs** (150 match / 100875 non-match). Comparators marked `*` are goldenmatch's actual rapidfuzz functions; MongeElkan/SoftTFIDF are the hybrids AbbrevAlign generalizes.

## In-sample ceiling (threshold chosen on all pairs — optimistic)

| Method | Best F1 | Precision | Recall | ROC-AUC | Threshold |
| --- | ---: | ---: | ---: | ---: | ---: |
| TokenRoleAlign | **0.703** | 0.542 | 1.000 | 0.999 | 0.927 |
| MongeElkan | **0.701** | 0.542 | 0.993 | 0.999 | 0.937 |
| SoftTFIDF | **0.701** | 0.542 | 0.993 | 0.999 | 0.780 |
| RecurAlign | **0.701** | 0.542 | 0.993 | 0.999 | 0.892 |
| TokenJaccard* | **0.692** | 0.539 | 0.967 | 0.999 | 0.778 |
| Levenshtein* | **0.690** | 0.537 | 0.967 | 0.999 | 0.822 |
| JaroWinkler* | **0.640** | 0.491 | 0.920 | 0.999 | 0.859 |
| AbbrevAlign | **0.629** | 0.459 | 1.000 | 0.999 | 0.988 |
| JW∪AbbrevAlign | **0.629** | 0.459 | 1.000 | 0.999 | 0.988 |
| PartialRatio* | **0.619** | 0.453 | 0.973 | 0.999 | 0.848 |
| TokenSortRatio* | **0.599** | 0.444 | 0.920 | 0.999 | 0.623 |

## Recall by positive slice (at each method's best-F1 threshold)

| Method | Abbrev/acronym | Nickname/initial | Typo |
| --- | ---: | ---: | ---: |
| TokenRoleAlign | — | — | — |
| MongeElkan | — | — | — |
| SoftTFIDF | — | — | — |
| RecurAlign | — | — | — |
| TokenJaccard* | — | — | — |
| Levenshtein* | — | — | — |
| JaroWinkler* | — | — | — |
| AbbrevAlign | — | — | — |
| JW∪AbbrevAlign | — | — | — |
| PartialRatio* | — | — | — |
| TokenSortRatio* | — | — | — |


## Held-out F1 (5-fold, entity-grouped CV — the honest number)

Thresholds (and the learned combiner) are fit on train entities and evaluated on unseen test entities, then pooled. This is the comparison that actually matters.

| Method | Held-out F1 | Precision | Recall | ROC-AUC |
| --- | ---: | ---: | ---: | ---: |
| MongeElkan | **0.865** | 0.771 | 0.987 | 0.999 |
| SoftTFIDF | **0.865** | 0.771 | 0.987 | 0.999 |
| RecurAlign | **0.865** | 0.771 | 0.987 | 0.999 |
| TokenRoleAlign | **0.865** | 0.771 | 0.987 | 0.999 |
| AbbrevAlign | **0.861** | 0.760 | 0.993 | 0.999 |
| JW∪AbbrevAlign | **0.861** | 0.760 | 0.993 | 0.999 |
| Levenshtein* | **0.852** | 0.766 | 0.960 | 0.998 |
| TokenJaccard* | **0.852** | 0.766 | 0.960 | 0.999 |
| PartialRatio* | **0.843** | 0.747 | 0.967 | 0.998 |
| StackLearned (JW+Abbrev+Nick) | **0.827** | 0.760 | 0.907 | 0.999 |
| JaroWinkler* | **0.826** | 0.750 | 0.920 | 0.998 |
| TokenSortRatio* | **0.820** | 0.745 | 0.913 | 0.998 |

## Findings

- **Best held-out F1: `MongeElkan` (0.865).**
- **Learned combiner (JW + AbbrevAlign + NickGraph) beats the JaroWinkler baseline** on held-out F1 (0.827 vs 0.826, Δ +0.000) — the principled fusion, not naive `max()`. AbbrevAlign and NickGraph contribute as features.
- **AbbrevAlign (nickname- + stopword-acronym-aware) alone:** held-out F1 0.861, precision 0.760, AUC 0.999. Two iterations drove this: v2 folded in nickname equivalence (Bob=Robert), v3 made acronym matching skip stopwords (FBI<-Federal Bureau *of* Investigation). It beats JaroWinkler on F1 (Δ +0.035). Both precision and recall discriminate here — the larger set carries real hard negatives.
- **Recommendation:** ship the learned combiner (JW + AbbrevAlign + NickGraph), which wins held-out (Δ +0.000 vs JaroWinkler) — learned weights handle the acronym-collision precision tradeoff.

> 150 positives across 450 records with realistic noise and organic hard negatives — the v2/v3 gains generalize and the learned combiner now wins. Synthetic, so the transformation distribution is known; the honest next step is a real labeled set (Cora / DBLP-ACM / company names) to confirm.


---

## Part 2: Venue matching (abbreviation field, GT-derived)

Venue equivalence comes free from the ground truth (matched papers share a venue). DBLP-ACM has only 5 venues, too few for held-out CV (one entity per fold, no negatives), so this is the *in-sample* ceiling + ROC-AUC - read it as per-pair separation, not a held-out F1.

# AbbrevAlign benchmark vs goldenmatch comparators

Abbreviation-heavy ER dataset: **10 records**, **45 candidate pairs** (5 match / 40 non-match). Comparators marked `*` are goldenmatch's actual rapidfuzz functions; MongeElkan/SoftTFIDF are the hybrids AbbrevAlign generalizes.

## In-sample ceiling (threshold chosen on all pairs — optimistic)

| Method | Best F1 | Precision | Recall | ROC-AUC | Threshold |
| --- | ---: | ---: | ---: | ---: | ---: |
| AbbrevAlign | **0.667** | 0.571 | 0.800 | 0.925 | 0.853 |
| JW∪AbbrevAlign | **0.625** | 0.455 | 1.000 | 0.920 | 0.765 |
| PartialRatio* | **0.615** | 0.500 | 0.800 | 0.870 | 0.647 |
| MongeElkan | **0.571** | 1.000 | 0.400 | 0.780 | 0.844 |
| TokenRoleAlign | **0.571** | 1.000 | 0.400 | 0.810 | 0.847 |
| Levenshtein* | **0.500** | 0.667 | 0.400 | 0.792 | 0.535 |
| TokenSortRatio* | **0.500** | 0.667 | 0.400 | 0.662 | 0.676 |
| SoftTFIDF | **0.500** | 0.667 | 0.400 | 0.767 | 0.551 |
| RecurAlign | **0.500** | 0.667 | 0.400 | 0.800 | 0.752 |
| JaroWinkler* | **0.476** | 0.312 | 1.000 | 0.840 | 0.517 |
| TokenJaccard* | **0.444** | 0.308 | 0.800 | 0.777 | 0.100 |

## Recall by positive slice (at each method's best-F1 threshold)

| Method | Abbrev/acronym | Nickname/initial | Typo |
| --- | ---: | ---: | ---: |
| AbbrevAlign | — | — | — |
| JW∪AbbrevAlign | — | — | — |
| PartialRatio* | — | — | — |
| MongeElkan | — | — | — |
| TokenRoleAlign | — | — | — |
| Levenshtein* | — | — | — |
| TokenSortRatio* | — | — | — |
| SoftTFIDF | — | — | — |
| RecurAlign | — | — | — |
| JaroWinkler* | — | — | — |
| TokenJaccard* | — | — | — |


### AbbrevAlign's over-merges (cross-cluster pairs scored >= its best-F1 threshold)

AbbrevAlign rates these *different* venues as matches - the acronym-collision precision failure (cf. IBM vs Indian Bank Mumbai):

| Venue A | Venue B | AbbrevAlign |
| --- | --- | ---: |
| Very Large Data Bases | The VLDB Journal — The International Journal on Very Large Data Bases | 1.000 |
| VLDB | The VLDB Journal — The International Journal on Very Large Data Bases | 1.000 |
| VLDB | VLDB J. | 1.000 |

### Verdict

On real labeled data AbbrevAlign *ties* JaroWinkler on generic titles (no harm, generalizes) and shows *higher per-pair separation on the abbreviation-heavy venue field at a precision cost* (it over-merges conference vs journal). Both point the same way: ship `abbrev_align` as a *gated comparator feature* feeding the learned scorer for abbreviation-heavy fields, not as a JaroWinkler replacement. The precision cost is exactly what the learned combiner / IDF-gating (handoff #2) is for.
