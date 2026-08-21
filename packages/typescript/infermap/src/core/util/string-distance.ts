// String-distance primitives — single-sourced from goldenmatch's scorer.
//
// These used to be VENDORED copies (a local Jaro / Jaro-Winkler / Levenshtein).
// They are now re-exports of goldenmatch's implementations, which are the
// parity-gated pure-TS fallback for the `goldenmatch-score-core` Rust kernel
// (`tests/parity/scorer-rapidfuzz.test.ts`, floating-point-identical to
// rapidfuzz at 5.6e-17 over a 2005-pair sweep). Single-kernel-collapse R5:
// one authoritative implementation per primitive, no second copy to drift.
//
// This also restores Python<->TS parity for infermap: the Python `fuzzy_name`
// scorer already reuses `goldenmatch-score-core::jaro_winkler_similarity`
// (infermap-core -> score-core path dep), so the TS port was the only surface
// still running its own copy.
//
// THREE real divergences the vendored copies carried, now fixed by
// construction. All three are the pre-#879 bugs: goldenmatch fixed them in its
// own scorer and the vendored fork never got the memo — which is precisely the
// drift a second copy produces.
//   * Transposition count — the local `jaroSimilarity` used an UNFLOORED
//     `transpositions / 2`; rapidfuzz (and score-core) use floored `⌊t/2⌋`.
//     This is the one that bites real inputs: `jaroWinkler("saturday",
//     "sunday")` was 0.7475, is now 0.7775 — the exact pair and values
//     goldenmatch's #879 note cites as its own fix.
//   * Winkler boost threshold — the local copy boosted at `jaro >= 0.7`;
//     rapidfuzz boosts only at **strict** `jaro > 0.7`. Differs only when jaro
//     is exactly 0.7, so it is rare but silently wrong when hit.
//   * Codepoint handling — the local copies indexed UTF-16 code units
//     (`s[i]`, `s.length`); goldenmatch's use `Array.from`, so non-BMP input
//     (emoji, astral-plane chars) now scores like the Python/Rust reference.
//
// Measured old-vs-new over a 75.7K-pair sweep (generated tokens + real column
// names + non-BMP): 57 jaro/jaro-winkler and 62 levenshtein disagreements, all
// non-BMP; a 240K-pair ASCII-only sweep found zero, so ASCII column names —
// infermap's normal input — are unaffected apart from the transposition class.
//
// The former `jaroWinklerSimilarity(s1, s2, prefixScale)` third argument is
// gone: no call site in the repo ever passed it, and a tunable prefix scale is
// by definition not the kernel-parity behaviour.
//
// The import is the narrow `goldenmatch/core/string-distance` subpath (a
// zero-import leaf), NOT the `goldenmatch/core` barrel — the barrel drags
// `core/scorer.ts`'s reference-data tables and WASM registry. `goldenmatch`
// stays a devDependency: tsup bundles it (`noExternal`), exactly as it already
// bundles `goldenmatch-wasm-runtime`, so infermap gains no published runtime
// dependency.
//
// These are re-exported as ANNOTATED const aliases rather than a bare
// `export { … } from`. tsup's `dts.resolve` does not follow a subpath specifier
// into the sibling package, so a bare re-export leaks
// `from 'goldenmatch/core/string-distance'` into the published .d.ts — an
// unresolvable type reference for consumers, since goldenmatch is not a runtime
// dep. The explicit signatures emit a self-contained declaration. They are
// direct aliases, not wrappers: no extra call frame, and no place for the
// signature to drift silently (a change to the kernel's shape fails typecheck
// here).
import {
  jaro,
  jaroWinkler,
  levenshteinDistance as levenshteinDistanceImpl,
} from "goldenmatch/core/string-distance";

export const jaroSimilarity: (a: string, b: string) => number = jaro;
export const jaroWinklerSimilarity: (a: string, b: string) => number =
  jaroWinkler;
export const levenshteinDistance: (a: string, b: string) => number =
  levenshteinDistanceImpl;
