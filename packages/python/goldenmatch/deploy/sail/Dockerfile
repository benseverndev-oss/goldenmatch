# Custom Sail image = LakeSail Spark Connect server + goldenmatch[sail] + the R1
# native kernel, so BOTH the rapidfuzz scorer and `score_field_pairwise` resolve
# on the driver pod AND the on-demand worker pods (PySpark Python UDFs run in the
# worker's own Python env -- a stock `sail` image has no goldenmatch).
#
# SCAFFOLD -- authored without a GKE cluster to shake it down. VERIFY before use:
#   * LakeSail base-image coordinates DRIFT. Check the current published tag at
#     https://docs.lakesail.com/sail/latest/guide/deployment/kubernetes.html and
#     https://github.com/lakehq/sail . The base MUST ship a Python 3.11+ interp
#     (it executes the PySpark UDF workers); if it doesn't, invert this image:
#     FROM python:3.12-slim + install the `sail` binary, then pip-install below.
#   * If the base python is "externally managed" (PEP 668), add
#     `--break-system-packages` to the pip install.
ARG SAIL_IMAGE=ghcr.io/lakehq/sail:latest
FROM ${SAIL_IMAGE}

# [sail] -> pysail + pyspark[connect]; [native] -> the published R1 wheel
# (goldenmatch-native, which carries score_field_pairwise once #985's wheel is
# released; until then the sail scorer falls back to the pure rapidfuzz floor --
# safe, just slower). Pin a version at deploy time for reproducible benches.
ARG GOLDENMATCH_VERSION=""
RUN pip install --no-cache-dir "goldenmatch[sail,native]${GOLDENMATCH_VERSION:+==$GOLDENMATCH_VERSION}" \
 && python -c "import goldenmatch, rapidfuzz; print('goldenmatch', goldenmatch.__version__)" \
 && python -c "from goldenmatch.core._native_loader import native_module as n; m=n(); print('native score_field_pairwise:', bool(m) and hasattr(m,'score_field_pairwise'))"

# Turn the R1 native scorer ON for the cluster (default-off otherwise: the
# `sail_scoring` component isn't in the loader's _GATED_ON allowlist). If the
# published wheel lacks the symbol, the scorer transparently uses the pure floor.
ENV GOLDENMATCH_NATIVE=1
