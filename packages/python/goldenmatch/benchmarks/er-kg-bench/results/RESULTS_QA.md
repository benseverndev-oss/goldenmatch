# ER-KG-Bench -- QA fact-completeness (SP6)

Does resolution buy complete retrieval? Mean fraction of an entity's gold
facts co-located where a query for it lands -- resolved KGs put all facts on
one node; an exact-match KG strands them across surface forms.

> The QA layer is **authored / synthetic** (facts hand-attached to surface forms). This measures the *fact co-location* that causes the `(ER_accuracy)^hops` decay -- NOT real-world QA accuracy, and NOT the hop-exponent (the KG model has no edges to traverse).

| Engine | status | mean fact-completeness | mean correctness (LLM) |
|---|---|---|---|
| goldengraph | ok | **0.533** | - |
| exact-match-floor | ok | **0.333** | - |

## Per-failure-class fact-completeness

| Engine | abbreviation | nickname_alias | synonym_brand |
|---|---|---|---|
| exact-match-floor | 0.33 | 0.33 | 0.33 |
| goldengraph | 0.58 | 0.67 | 0.33 |
