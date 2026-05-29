# Snowflake Cortex -- in-warehouse embeddings + LLM

This package treats Snowflake Cortex as a **first-class peer** to
Vertex AI, OpenAI, and the local sentence-transformers embedder.
Same `EmbeddingProvider` contract, same `embed_records()` entry
point, same cache namespacing -- but the embedding step happens
inside the customer's Snowflake account.

## Why use Cortex for embeddings

| Compared to | Cortex wins on |
|---|---|
| Vertex AI / OpenAI | Zero data egress -- PII never leaves the Snowflake VPC. No external API keys to rotate. One billing relationship (Cortex credits roll up to warehouse spend). |
| Local sentence-transformers | No model download. No HuggingFace token. No GPU on the dbt-runner host. Latency is one Snowflake round-trip, not a 100 MB cold start. |
| Bloom-filter PPRL | Real semantic similarity instead of token-prefix overlap. Catches the cases (typos, abbreviations, transliterations) that pure-token PPRL misses while keeping the egress-free posture. |

Where Cortex loses: out-of-warehouse callers (a Vercel function,
the SIFT app, an Airflow job) can't reach it without a Snowflake
session. For those paths Vertex / OpenAI / local remain the right
providers. The dispatch table in
`goldenmatch.embeddings.resolve_provider` covers all four
interchangeably.

## Python provider

```python
from goldenmatch.embeddings import embed_records

# Implicit -- pulls SNOWFLAKE_* env vars (account / user / token /
# warehouse / role) the same way the rest of goldenmatch's
# Snowflake connector code does.
vecs = embed_records(
    ["Alice Smith", "A. Smith", "Bob Jones"],
    provider="snowflake_cortex",
    model="snowflake-arctic-embed-m-v1.5",
)

# Explicit -- pass a live snowflake.connector.connection. Useful
# when goldenmatch is running inside dbt and the adapter already
# owns a connection.
from goldenmatch.embeddings import SnowflakeCortexProvider
import snowflake.connector

conn = snowflake.connector.connect(...)
prov = SnowflakeCortexProvider(connection=conn, model="e5-base-v2")
vecs = embed_records(texts, provider=prov)
```

Supported models (catalog as of 2026-05; pass `model_dim=` to
register others):

| Model | Dim |
|---|---|
| `snowflake-arctic-embed-xs` | 384 |
| `snowflake-arctic-embed-s` | 384 |
| `snowflake-arctic-embed-m` | 768 |
| `snowflake-arctic-embed-m-v1.5` (default) | 768 |
| `snowflake-arctic-embed-l-v2.0` | 1024 |
| `e5-base-v2` | 768 |
| `nv-embed-qa-4` | 1024 |
| `voyage-multilingual-2` | 1024 |
| `multilingual-e5-large` | 1024 |

The provider caches by `model_id + text_hash` (same as the other
providers), so identical normalized texts embed once even across
multiple calls.

## dbt macros

Five Snowflake-only macros wrap the Cortex SQL functions for use
inside dbt models:

| Macro | SQL function |
|---|---|
| `cortex_embed_768(column, model='snowflake-arctic-embed-m-v1.5')` | `SNOWFLAKE.CORTEX.EMBED_TEXT_768(model, column)` |
| `cortex_embed_1024(column, model='snowflake-arctic-embed-l-v2.0')` | `SNOWFLAKE.CORTEX.EMBED_TEXT_1024(model, column)` |
| `cortex_embed(column, model, dim)` | dispatches to the right `EMBED_TEXT_<dim>` |
| `cortex_cosine_similarity(vec_a, vec_b)` | `VECTOR_COSINE_SIMILARITY(...)` |
| `cortex_l2_distance(vec_a, vec_b)` | `VECTOR_L2_DISTANCE(...)` |
| `cortex_inner_product(vec_a, vec_b)` | `VECTOR_INNER_PRODUCT(...)` |
| `cortex_complete(prompt, model='llama3.1-8b')` | `SNOWFLAKE.CORTEX.COMPLETE(model, prompt)` |

### Recipe: pre-materialize vectors, then dedupe on similarity

```sql
-- models/staging/stg_customers_embedded.sql
{{ config(materialized='table') }}

select
    customer_id,
    name,
    address,
    {{ dbt_goldensuite.cortex_embed_768(
        "name || ' ' || trim(address)",
        model='snowflake-arctic-embed-m-v1.5'
    ) }} as identity_vec
from {{ ref('stg_customers') }}
```

```sql
-- models/marts/customer_pairs.sql
with pairs as (
    select
        a.customer_id as id_a,
        b.customer_id as id_b,
        {{ dbt_goldensuite.cortex_cosine_similarity(
            'a.identity_vec', 'b.identity_vec'
        ) }} as similarity
    from {{ ref('stg_customers_embedded') }} a
    join {{ ref('stg_customers_embedded') }} b
        on a.customer_id < b.customer_id
)
select * from pairs where similarity >= 0.85
```

### Recipe: LLM-boost borderline pairs

Use `cortex_complete` to second-guess the 0.75-0.90 band without
sending PII to an external LLM:

```sql
select
    id_a,
    id_b,
    similarity,
    {{ dbt_goldensuite.cortex_complete(
        "'Are these the same person? '"
        " || (select name from customers where id = pairs.id_a) || ' vs '"
        " || (select name from customers where id = pairs.id_b)"
        " || '. Reply YES or NO only.'",
        model='llama3.1-8b'
    ) }} as llm_verdict
from {{ ref('customer_pairs') }}
where similarity between 0.75 and 0.90
```

## Verified shapes against live Cortex

| Test | Result |
|---|---|
| `EMBED_TEXT_768('snowflake-arctic-embed-m-v1.5', 'hello world')` | Returns 768-float vector. |
| `VECTOR_COSINE_SIMILARITY(Alice, A. Smith)` | 0.91 |
| `VECTOR_COSINE_SIMILARITY(Alice, Bob Jones)` | 0.84 |
| `VECTOR_L2_DISTANCE(Alice, A. Smith)` | 0.42 |
| `VECTOR_INNER_PRODUCT(Alice, A. Smith)` | 0.91 |
| `COMPLETE('llama3.1-8b', 'Are these the same person...')` | "Yes." |

Numerical values match a numpy round-trip (`np.allclose` between
the raw Cortex result and the Python provider's output).

## Cost shape

Cortex EMBED_TEXT is billed per million tokens against your
account's Cortex credits. As of 2026, the `snowflake-arctic-embed-*`
family is the cheapest tier in Cortex; third-party models
(`nv-embed-qa-4`, `voyage-*`) cost more per token. Snowflake
publishes the per-credit-per-million-token rates in their docs.

Practical cost framing for ER workloads:

- Average person record is ~50 tokens (name + address + email).
- 1M records ≈ 50M tokens ≈ a few dollars on the Snowflake-family
  models.
- Caching (`text_hash`-keyed) means re-runs cost ~0 for
  unchanged records -- only deltas re-embed.

`VECTOR_COSINE_SIMILARITY` / `VECTOR_L2_DISTANCE` /
`VECTOR_INNER_PRODUCT` are warehouse-compute (not Cortex credits),
so the similarity scoring step is just normal warehouse spend.

## Caveats

- Cortex EMBED_TEXT_768/1024 are scalar functions -- they don't
  batch over rows on the SQL side. The Python provider batches at
  the connector layer (default chunk size 500) to amortize
  per-call latency.
- Models occasionally roll over. `snowflake-arctic-embed-m-v1.5`
  has been stable, but pin the model version in your config to
  guard against cache invalidation when a new version ships.
- The `cortex_complete` LLM models change more often than the
  embedder models. Treat `llama3.1-8b` as the lowest-common-
  denominator default and bump intentionally.
