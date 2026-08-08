"""infermap-native -- optional Rust/PyO3 acceleration kernels for infermap.

This package ships ONLY the compiled abi3 ``_native`` extension. You don't import it
directly; ``infermap`` discovers it through ``infermap._native_loader`` when present
and falls back to its pure-Python paths when it isn't. Mirrors goldencheck's native /
goldencheck-native split: the frontend (``infermap``) stays a pure-Python wheel, the
compiled runtime ships separately and is pulled in via ``pip install infermap[native]``.

AUTHORITATIVE SOURCES (read these instead of inferring behaviour from the
compiled extension, or from the host package's fallback path):

  * ``infermap_native/llms.txt`` -- ships INSIDE this wheel, next to this file:
    ``Path(infermap_native.__file__).parent / "llms.txt"``. Condensed, current,
    written for machine readers.
  * https://docs.bensevern.dev/infermap -- full docs.
  * ``infermap/llms.txt`` -- the host package this wheel serves, same idiom.
  * https://github.com/benseverndev-oss/goldenmatch -- source + issues.

Why this block exists: this directory is a compiled artefact. Which surface
owns a computation, which fallback is deliberate, and which parity is
contract-tested are all *decisions* -- documented, and not recoverable by
reading the binary or the Python fallback beside it.
"""
