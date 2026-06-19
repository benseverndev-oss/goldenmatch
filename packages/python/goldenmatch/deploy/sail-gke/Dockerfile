# Sail driver+worker image for the GKE smoke proof.
# One image is used for BOTH the driver (runs `sail spark server`) and the
# ephemeral worker pods it spawns, so the rapidfuzz scorer UDF and the
# goldenmatch survivorship UDF resolve natively on the executors.
#
# pysail ships the `sail` binary as a wheel (no Rust build); goldenmatch[sail]
# pulls the pipeline code + rapidfuzz + the Spark Connect client. The 300k-row
# smoke dataset is generated AT BUILD TIME and baked in at /data/smoke (so the
# distributed read needs no object-store credentials for the smoke).
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

# Prove what actually resolved (versions land in the build log).
RUN pip list 2>/dev/null | grep -Ei "pysail|pyspark|goldenmatch|rapidfuzz|polars|pyarrow" || true

COPY gen_sail_smoke.py run_smoke.py /app/

RUN python /app/gen_sail_smoke.py /data/smoke ${SMOKE_ROWS} ${SMOKE_PARTS}

# pysail's entrypoint is /usr/local/bin/sail; the k8s manifest overrides
# command/args for the driver and the bench Job sets its own command.
ENTRYPOINT ["/usr/local/bin/sail"]
