# Changelog

All notable changes to `infermap` are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/); versioning
follows [Semantic Versioning](https://semver.org/).

> This file was started at 0.7.0. Releases before that are not reconstructed
> here — see `git log -- packages/typescript/infermap` for the earlier history.

## [Unreleased]

### Changed

- **String-distance primitives are now single-sourced from goldenmatch's
  parity-gated scorer** (single-kernel-collapse R5). `jaroSimilarity`,
  `jaroWinklerSimilarity`, and `levenshteinDistance` — exported from
  `infermap/core` and used by the `fuzzy_name` scorer — were locally vendored
  implementations; they now alias `goldenmatch/core/string-distance`, which is
  the conformance-tested pure-TS fallback for the `goldenmatch-score-core` Rust
  kernel. This also restores Python↔TS parity: infermap's Python `fuzzy_name`
  already reuses `goldenmatch-score-core::jaro_winkler_similarity`, so the TS
  port was the only surface still running its own math.

  **Scores change in three cases.** The vendored copies predated goldenmatch's
  rapidfuzz-alignment fix and carried all three of its bugs:

  | | vendored | now (rapidfuzz / score-core) |
  |---|---|---|
  | transposition count | unfloored `t / 2` | floored `⌊t/2⌋` |
  | Winkler boost threshold | `jaro >= 0.7` | strict `jaro > 0.7` |
  | character iteration | UTF-16 code units | codepoints |

  The transposition fix is the one that moves ordinary inputs — e.g.
  `jaroWinklerSimilarity("saturday", "sunday")` was `0.7475`, is now `0.7775`.
  Measured over a 75.7K-pair sweep, 57 Jaro/Jaro-Winkler and 62 Levenshtein
  results changed, all on non-BMP input; a 240K-pair ASCII-only sweep found no
  change beyond the transposition class. Column names — infermap's normal input
  — are ASCII in practice, so mapping results are expected to be stable, but a
  caller that pinned exact scores may see the values above shift.

  No runtime dependency was added: `goldenmatch` remains a devDependency and the
  (zero-import) primitives are inlined at build time.

### Removed

- **`jaroWinklerSimilarity`'s third `prefixScale` argument.** It defaulted to
  `0.1` and no caller ever passed it; a tunable prefix scale is by definition
  not the kernel-parity behaviour. Calls passing a third argument are a type
  error now — drop it to keep the previous default.
