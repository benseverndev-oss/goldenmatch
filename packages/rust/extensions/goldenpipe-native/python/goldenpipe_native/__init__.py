"""goldenpipe-native: PyO3 binding over goldenpipe-core. The compiled `_native` ext lands here.

AUTHORITATIVE SOURCES (read these instead of inferring behaviour from the
compiled extension, or from the host package's fallback path):

  * ``goldenpipe_native/llms.txt`` -- ships INSIDE this wheel, next to this file:
    ``Path(goldenpipe_native.__file__).parent / "llms.txt"``. Condensed, current,
    written for machine readers.
  * https://docs.bensevern.dev/docs/goldenpipe -- full docs.
  * ``goldenpipe/llms.txt`` -- the host package this wheel serves, same idiom.
  * https://github.com/benseverndev-oss/goldenmatch -- source + issues.

Why this block exists: this directory is a compiled artefact. Which surface
owns a computation, which fallback is deliberate, and which parity is
contract-tested are all *decisions* -- documented, and not recoverable by
reading the binary or the Python fallback beside it.
"""
