# Snowpark Container Services (SPCS) -- native acceleration path

The Snowpark Python UDF path in [snowflake-setup.md](snowflake-setup.md)
ships pure-Python `goldenmatch` -- Anaconda's Snowflake channel
doesn't carry `goldenmatch-native`. For workloads where the native
Rust kernel matters (large dedupe runs, embedding-heavy scoring,
LLM-boosted pipelines), the SPCS path is the supported alternative.

Same dbt macros, same SQL surface, same `goldenmatch` schema -- the
only thing that changes is what's behind the function name. The
Python UDFs become **service functions** that POST batched requests
to a container running the goldenmatch native kernel.

## What lives in `spcs/`

| File | Purpose |
|---|---|
| `Dockerfile` | Builds the image: Python 3.11 + `goldenmatch[native]` + Flask + gunicorn. Pin `GOLDENMATCH_VERSION` via build arg. |
| `server.py` | Thin Flask app exposing one POST endpoint per UDF. Implements Snowflake's documented batched-request contract: `{"data": [[row_idx, ...args], ...]}` in, same shape out. |
| `service-spec.yaml` | SPCS service spec -- container, compute pool, volume mount for the identity DB, no public endpoint. |

## Status: structural scaffold (not deploy-verified in this PR)

The Dockerfile, HTTP contract, batching shape, and route table are
complete. The per-operation handler bodies in `server.py` are
intentionally stubbed -- each one maps cleanly onto an existing
goldenmatch / goldencheck / goldenflow Python entry point but the
attribute names should be re-verified against the installed version
before going to production. Stubs are marked with
`# TODO(spcs): wire to ...` for grep.

What this PR does NOT do:

- Build + push the image to a real Snowflake image registry
- Create the compute pool, image repository, OAuth integration, or
  the service itself against a live account
- Verify the service function call shape end-to-end from a
  Snowflake worksheet

The deploy walkthrough below is the documented path; treat it as a
runbook for the first deployment rather than a record of one that
has happened.

## Deploy walkthrough

### 1. Prerequisites

- Snowflake account on Enterprise or higher (SPCS requirement).
- `ACCOUNTADMIN` to create the compute pool + image registry
  privileges once; routine pushes can use a less-privileged role
  after grants are in place.

### 2. One-time account setup (ACCOUNTADMIN)

```sql
USE ROLE ACCOUNTADMIN;

-- The role that will own the compute pool, service, and registry.
CREATE ROLE IF NOT EXISTS goldenmatch_spcs_admin;
GRANT ROLE goldenmatch_spcs_admin TO ROLE <your_role>;

-- Compute pool the service runs in. Size to match your dedupe
-- workload -- HIGHMEM_X64_M is a reasonable starting point for
-- 1-25M rows; bump to L for heavier loads.
CREATE COMPUTE POOL goldenmatch_pool
    MIN_NODES = 1
    MAX_NODES = 2
    INSTANCE_FAMILY = HIGHMEM_X64_M
    AUTO_SUSPEND_SECS = 300;

GRANT USAGE ON COMPUTE POOL goldenmatch_pool
    TO ROLE goldenmatch_spcs_admin;
GRANT MONITOR ON COMPUTE POOL goldenmatch_pool
    TO ROLE goldenmatch_spcs_admin;

-- Image registry in the same database as the dbt macros expect.
USE DATABASE <target_database>;
CREATE SCHEMA IF NOT EXISTS goldenmatch;
USE SCHEMA goldenmatch;

CREATE IMAGE REPOSITORY IF NOT EXISTS goldenmatch_images;
GRANT READ, WRITE ON IMAGE REPOSITORY goldenmatch_images
    TO ROLE goldenmatch_spcs_admin;

-- Stage for the service spec + a persistent volume for identity.db.
CREATE STAGE IF NOT EXISTS goldenmatch_specs
    DIRECTORY = (ENABLE = TRUE);
CREATE STAGE IF NOT EXISTS goldenmatch_data
    DIRECTORY = (ENABLE = TRUE)
    ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE');

GRANT READ, WRITE ON STAGE goldenmatch_specs
    TO ROLE goldenmatch_spcs_admin;
GRANT READ, WRITE ON STAGE goldenmatch_data
    TO ROLE goldenmatch_spcs_admin;
```

### 3. Build + push the image

From a checkout of this repo:

```bash
cd packages/python/goldenmatch/dbt-goldensuite/spcs/

# Build.
docker build \
    --build-arg GOLDENMATCH_VERSION=1.15.0 \
    -t goldenmatch-spcs:1.15.0 .

# Auth + tag + push. Snowflake's image registry URL is per-account:
#   <account>.registry.snowflakecomputing.com
SF_REG=<account>.registry.snowflakecomputing.com
SF_REPO=$SF_REG/<database>/goldenmatch/goldenmatch_images
docker login $SF_REG -u <your_user>   # password = Snowflake password / PAT
docker tag goldenmatch-spcs:1.15.0 $SF_REPO/goldenmatch-spcs:1.15.0
docker push $SF_REPO/goldenmatch-spcs:1.15.0
```

### 4. Upload the service spec

```bash
# From the spcs/ directory.
snowsql -q "PUT file://service-spec.yaml @goldenmatch.goldenmatch_specs OVERWRITE = TRUE"
```

Edit the `<account>`, `<database>`, `<schema>`, `<repo>`, and `<tag>`
placeholders in `service-spec.yaml` before uploading -- SPCS won't
expand them at runtime.

### 5. Create the service

```sql
USE ROLE goldenmatch_spcs_admin;
USE DATABASE <target_database>;
USE SCHEMA goldenmatch;

CREATE SERVICE goldenmatch_service
    IN COMPUTE POOL goldenmatch_pool
    FROM @goldenmatch.goldenmatch_specs
    SPECIFICATION_FILE = 'service-spec.yaml'
    MIN_INSTANCES = 1
    MAX_INSTANCES = 1;

-- Wait for the service to come up.
CALL SYSTEM$WAIT_FOR_SERVICES(300, 'goldenmatch_service');
SHOW SERVICES LIKE 'goldenmatch_service';

-- Tail the container log while you smoke-test.
SELECT SYSTEM$GET_SERVICE_LOGS(
    'goldenmatch_service', 0, 'goldenmatch', 200
);
```

### 6. Create the service functions

These look like the regular Snowpark Python UDFs in
[snowflake-setup.md](snowflake-setup.md) but use the `SERVICE` /
`ENDPOINT` / `AS '/...'` clauses instead of `LANGUAGE PYTHON`:

```sql
-- Scalar: identity_resolve.
CREATE OR REPLACE FUNCTION goldenmatch.goldenmatch_identity_resolve(
    record_id STRING,
    db_path   STRING
)
RETURNS VARIANT
SERVICE = goldenmatch.goldenmatch_service
ENDPOINT = 'api'
AS '/identity-resolve';

-- Scalar: goldencheck_health_score.
CREATE OR REPLACE FUNCTION goldenmatch.goldencheck_health_score(
    relation STRING
)
RETURNS FLOAT
SERVICE = goldenmatch.goldenmatch_service
ENDPOINT = 'api'
AS '/goldencheck-health-score';

-- UDTF: dedupe_full (clusters + pairs follow the same shape).
CREATE OR REPLACE FUNCTION goldenmatch.goldenmatch_dedupe_full(
    input_table STRING,
    config_json STRING
)
RETURNS TABLE(cluster_id BIGINT, golden VARIANT)
SERVICE = goldenmatch.goldenmatch_service
ENDPOINT = 'api'
AS '/dedupe-full';

-- ... mirror the rest of snowflake-setup.md's CREATE FUNCTION
-- statements, replacing `LANGUAGE PYTHON ... IMPORTS = (...)`
-- with `SERVICE = ... ENDPOINT = ... AS '/<route>'`.
```

The route names match `server.py`'s `app.add_url_rule` table 1:1 --
hyphens between words.

### 7. Smoke test

```sql
-- Health check (round-trips through the SPCS ingress).
SELECT goldenmatch.goldencheck_health_score('my_table');

-- Smallest possible UDTF roundtrip.
CREATE TEMPORARY TABLE smoke_in AS SELECT 'alice' AS name;
SELECT *
FROM TABLE(goldenmatch.goldenmatch_dedupe_full('smoke_in', '{}'));
```

If both succeed, the dbt macros from this package work transparently
against the SPCS path -- nothing in `dbt_project.yml` or the macro
calls changes.

## Native acceleration

The image installs `goldenmatch[native]`, which pulls the
`goldenmatch-native` abi3 wheel from PyPI. The native loader in
`goldenmatch.core._native_loader` discovers the kernel automatically
under the default `GOLDENMATCH_NATIVE=auto`. Force-on for production
runs by setting `GOLDENMATCH_NATIVE=1` in `service-spec.yaml`'s
`env:` block -- the service will fail-fast if the wheel didn't land
in the image instead of silently falling back to pure Python.

## Cost shape

SPCS billing is per-second-of-compute-pool-uptime + per-GB egress.
The compute pool auto-suspends after `AUTO_SUSPEND_SECS = 300` so
quiet hours are free. Sizing notes:

| Workload | Recommended | Approximate $/hour while warm |
|---|---|---|
| Interactive dbt iterations (5-10K rows) | `CPU_X64_S`, MIN=0, MAX=1 | ~$0.30 |
| Daily-batch dedupe (100K-1M rows) | `HIGHMEM_X64_M`, MIN=0, MAX=2 | ~$1.50 |
| Heavy native dedupe (5-25M rows) | `HIGHMEM_X64_L`, MIN=0, MAX=2 | ~$6.00 |

(Snowflake publishes the per-credit costs per instance family in
their docs; convert via your account's per-credit rate.)

## Caveats

- SPCS images can only pull from Snowflake's own image registry --
  no DockerHub. The push step (#3) is non-skippable.
- Image rotation requires `DROP FUNCTION` + recreate when the
  `SERVICE` is replaced; the dbt-side macro call doesn't change.
- The container's network is locked to the Snowflake VNet; if
  goldenmatch's `[native]` loader tries to fetch anything (e.g.
  HuggingFace cross-encoder weights for rerank), it must be
  bundled into the image at build time.
- If your dbt models use `output='clusters'` or `output='pairs'`,
  the corresponding service functions must exist on the same
  service -- create all three (`/dedupe-full`, `/dedupe-clusters`,
  `/dedupe-pairs`) at deploy time.
