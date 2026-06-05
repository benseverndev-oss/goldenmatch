# Snowpark Container Services image for goldenmatch.
#
# Build context: this directory (`packages/python/goldenmatch/dbt-goldensuite/spcs/`).
# The image runs `server.py`, a thin Flask app exposing the same
# functions the Snowpark Python UDFs would, except backed by the
# native Rust kernel via `pip install goldenmatch[native]`.
#
# Push target: <account>.registry.snowflakecomputing.com/<db>/<schema>/<repo>/goldenmatch-spcs:<tag>
#
# Verified locally with `docker build .` + `docker run --rm -p 8080:8080 <tag>`
# and a curl POST to /dedupe-full. NOT verified against a live SPCS
# account in this PR -- see docs/snowflake-spcs.md for the deploy
# walkthrough.

FROM python:3.11-slim@sha256:a3ab0b966bc4e91546a033e22093cb840908979487a9fc0e6e38295747e49ac0

# Build-time hardening for native wheel installs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install goldenmatch + the native Rust kernel. The native wheel
# (`goldenmatch-native`) is a separate package on PyPI; the `[native]`
# extra pulls it in. Pinning is intentional -- a wheel rotation should
# go through a deliberate image rebuild + push, not a silent latest.
ARG GOLDENMATCH_VERSION=1.15.0
RUN pip install --no-cache-dir \
    "goldenmatch[native]==${GOLDENMATCH_VERSION}" \
    flask==3.0.3 \
    gunicorn==23.0.0

COPY server.py /app/server.py

# SPCS routes container traffic via the spec's `containers[].port` to
# whatever port the process binds. We listen on 8080 to match the
# default `endpoints.port` in service-spec.yaml.
EXPOSE 8080

# gunicorn with 4 workers + 2 threads. SPCS supplies enough CPU to
# saturate this; tune based on the compute_pool instance family in
# service-spec.yaml. Long-running dedupe calls -> 600s timeout.
CMD ["gunicorn", \
     "--bind", "0.0.0.0:8080", \
     "--workers", "4", \
     "--threads", "2", \
     "--timeout", "600", \
     "--access-logfile", "-", \
     "server:app"]
