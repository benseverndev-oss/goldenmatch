# Sail driver+worker image for the GKE proof, built with the lakehq/sail #2081 fix.
#
# WHY a source build: #2081 ("don't fail a pending task while workers are still
# launching") is a DRIVER-SCHEDULER fix in `crates/sail-execution`. It is on
# lakehq/sail `main` but UNRELEASED -- the latest pysail wheel (0.6.4, 2026-06-06)
# predates the merge (2026-06-17), so `pip install pysail==0.6.4` ships the binary
# WITHOUT the fix. We build the `sail` binary from the #2081 commit and drop it over
# the pysail-installed one, so the driver + the worker pods it spawns run the fix.
# The Python side (Spark Connect client, goldenmatch pipeline) stays on pysail 0.6.4
# -- the 0.6.x Spark Connect protocol is unchanged by a scheduler bugfix.
#
# One image is used for BOTH the driver (`command: ["sail"]`) and the ephemeral
# worker pods, so the rapidfuzz scorer UDF + the goldenmatch survivorship UDF
# resolve natively on the executors, AND every pod gets the #2081 binary.

# --- builder: compile the sail binary from the #2081 commit (mirrors Sail's own
#     docker/release/Dockerfile recipe) -----------------------------------------
ARG SAIL_RUST_VERSION=1.95.0
# Pinned to the #2081 merge commit for a reproducible build. Bump to a released
# pysail wheel + drop this whole builder stage once 0.6.5 (which includes #2081)
# ships on PyPI.
ARG SAIL_GIT_REF=3e6c419427cd80f47ffd7ec37fa8ebc8735f6f0a

FROM python:3.12-slim AS sail-builder
ARG SAIL_RUST_VERSION
ARG SAIL_GIT_REF

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates gcc libc6-dev \
        protobuf-compiler libprotobuf-dev \
        curl git pkg-config && \
    rm -rf /var/lib/apt/lists/*

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs > /tmp/rustup-init && \
    chmod a+x /tmp/rustup-init && \
    /tmp/rustup-init -y --no-modify-path --profile default \
        --default-toolchain ${SAIL_RUST_VERSION}
ENV PATH="/root/.cargo/bin:${PATH}"

WORKDIR /sail
# Fetch only the pinned commit (no full history) then check it out.
RUN git init -q && \
    git remote add origin https://github.com/lakehq/sail.git && \
    git fetch --depth 1 origin "${SAIL_GIT_REF}" && \
    git checkout -q FETCH_HEAD

# Build just the `sail` CLI binary (the driver/worker server), as Sail's release
# image does. This is the slow step (~10-25 min, full Rust workspace).
RUN cargo build -p sail-cli --release --bins && \
    cp /sail/target/release/sail /usr/local/bin/sail

# --- runtime ------------------------------------------------------------------
FROM python:3.12-slim

ARG PYSAIL_VERSION=0.6.4
ARG SMOKE_ROWS=300000
ARG SMOKE_PARTS=8

# setuptools: Python 3.12 dropped distutils; pyspark 3.5's pandas check imports
#   distutils.version.LooseVersion -> setuptools provides the shim.
# pandas/pyarrow pinned to ranges pyspark 3.5's pandas-UDF path expects (the
#   unconstrained resolve pulled pandas 3.0 + pyarrow 24, both too new for it).
RUN pip install --no-cache-dir \
        "pysail==${PYSAIL_VERSION}" \
        "goldenmatch[sail]" \
        "pyspark[connect]>=3.5,<4" \
        "setuptools>=70" \
        "pandas>=2.0,<2.3" \
        "pyarrow>=14,<18"

# Override the pysail-shipped (0.6.4, no fix) binary with the #2081 build so the
# driver and every worker pod run the scheduler fix. The Python package stays
# pysail 0.6.4 (client only).
COPY --from=sail-builder /usr/local/bin/sail /usr/local/bin/sail
RUN /usr/local/bin/sail --version 2>/dev/null || true

# Prove what actually resolved (versions + the source-built binary in the build log).
RUN pip list 2>/dev/null | grep -Ei "pysail|pyspark|goldenmatch|rapidfuzz|polars|pyarrow" || true

COPY gen_sail_smoke.py run_smoke.py /app/

# Connectivity smoke dataset baked in (every pod has /data/smoke, no object store).
# The 10M/100M SCALE test that actually exercises #2081 reads from a gs:// path -
# see RUN-NOTES-2081.md (a 300k baked dataset does NOT trigger the multi-stage
# worker scale-up the fix is about).
RUN python /app/gen_sail_smoke.py /data/smoke ${SMOKE_ROWS} ${SMOKE_PARTS}

# pysail's entrypoint is /usr/local/bin/sail; the k8s manifest overrides
# command/args for the driver and the bench Job sets its own command.
ENTRYPOINT ["/usr/local/bin/sail"]
