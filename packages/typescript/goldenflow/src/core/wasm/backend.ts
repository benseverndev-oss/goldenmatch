/**
 * backend.ts — opt-in WASM identifier-kernel backend registry. Edge-safe: no
 * node:* here.
 *
 * The active backend (if any) is consulted by the identifier transforms
 * (cc/iban/isbn/ean/vat/swift/aba/imei) for the 14 covered functions, the
 * i18n-name transforms (name_transliterate/name_script), the email
 * transforms (email_lowercase/normalize/extract_domain/validate), the URL
 * transforms (url_normalize/extract_domain), the categorical transforms
 * (boolean_normalize/gender_standardize/null_standardize/
 * category_normalize_key), and the address transforms (address_standardize/
 * address_expand/state_abbreviate/state_expand/zip_normalize/
 * country_standardize/unit_normalize/split_address), and the text transforms
 * (strip/collapse_whitespace/normalize_quotes/normalize_line_endings/
 * remove_html_tags/remove_urls/remove_digits/remove_punctuation/remove_emojis/
 * extract_numbers/truncate/pad_left/pad_right + the text-2 Unicode-heavy five
 * lowercase/uppercase/title_case/normalize_unicode/fix_mojibake), and the
 * category_auto_correct fuzzy kernel (fuzz_ratio + build_canonical_map);
 * everything else stays pure-TS.
 * Mirrors goldenmatch's `setScorerBackend(null)` module-singleton pattern for
 * test isolation.
 */

/**
 * A WASM-backed identifier kernel over the goldenflow-core validate/format/
 * mask/normalize functions (see `goldenflow-wasm/src/lib.rs`). Byte-identical
 * to the Python/native kernels by construction — this crate is a thin
 * wasm-bindgen shim over the SAME `goldenflow-core::identifiers` module.
 *
 * `string | undefined` mirrors the Rust `Option<String>` return of the
 * format/normalize/mask functions (wasm-bindgen maps `None` to `undefined`,
 * not `null`).
 */
export interface FlowWasmBackend {
  ccValidate(s: string): boolean;
  ccFormat(s: string): string | undefined;
  ccMask(s: string): string | undefined;
  ibanValidate(s: string): boolean;
  ibanFormat(s: string): string | undefined;
  isbnValidate(s: string): boolean;
  isbnNormalize(s: string): string | undefined;
  eanValidate(s: string): boolean;
  vatValidate(s: string): boolean;
  vatFormat(s: string): string | undefined;
  swiftValidate(s: string): boolean;
  swiftFormat(s: string): string | undefined;
  abaValidate(s: string): boolean;
  imeiValidate(s: string): boolean;
  nameTransliterate(s: string): string;
  nameScript(s: string): string;
  stripTitles(s: string): string;
  stripSuffixes(s: string): string;
  nameProper(s: string): string;
  nicknameStandardize(s: string): string;
  hasInitial(s: string): boolean;
  splitName(s: string): string[];
  splitNameReverse(s: string): string[];
  mergeName(first: string | null, last: string | null): string | undefined;
  emailLowercase(s: string): string;
  emailNormalize(s: string): string;
  emailExtractDomain(s: string): string | undefined;
  emailValidate(s: string): boolean;
  urlNormalize(s: string): string | undefined;
  urlExtractDomain(s: string): string | undefined;
  currencyStrip(s: string): number | undefined;
  percentageNormalize(s: string): number | undefined;
  toInteger(s: string): number | undefined;
  commaDecimal(s: string): number | undefined;
  scientificToDecimal(s: string): number | undefined;
  roundValue(x: number, n: number): number;
  clampValue(x: number, minVal: number, maxVal: number): number;
  absValue(x: number): number;
  fillZero(x: number | undefined): number;
  booleanNormalize(s: string): boolean | undefined;
  genderStandardize(s: string): string;
  nullStandardize(s: string): string | undefined;
  categoryNormalizeKey(s: string): string;
  addressStandardize(s: string): string;
  addressExpand(s: string): string;
  stateAbbreviate(s: string): string;
  stateExpand(s: string): string;
  zipNormalize(s: string): string;
  countryStandardize(s: string): string;
  unitNormalize(s: string): string;
  /** `split_address` -> 4-element `[street, city, state, zip]`; `street` is
   * always a string, the other three are `string | null` (Rust `Option`). */
  splitAddress(s: string): (string | null)[];
  strip(s: string): string;
  collapseWhitespace(s: string): string;
  normalizeQuotes(s: string): string;
  normalizeLineEndings(s: string): string;
  removeHtmlTags(s: string): string;
  removeUrls(s: string): string;
  removeDigits(s: string): string;
  removePunctuation(s: string): string;
  removeEmojis(s: string): string;
  extractNumbers(s: string): string;
  truncate(s: string, n: number): string;
  padLeft(s: string, width: number, pad: string): string;
  padRight(s: string, width: number, pad: string): string;
  lowercase(s: string): string;
  uppercase(s: string): string;
  titleCase(s: string): string;
  normalizeUnicode(s: string): string;
  fixMojibake(s: string): string;
  /** rapidfuzz `fuzz.ratio` (Indel/LCS similarity, 0-100). */
  fuzzRatio(a: string, b: string): number;
  /** category-autocorrect correction map from parallel `values`/`counts`
   * arrays (in value_counts DESC order). Returns a FLAT `[from0, to0, from1,
   * to1, ...]` array of correction pairs; the caller unflattens. */
  buildCanonicalMap(
    values: string[],
    counts: number[],
    freqThreshold: number,
    matchThreshold: number,
  ): string[];
}

import { createBackendRegistry } from "goldenmatch-wasm-runtime";

const _registry = createBackendRegistry<FlowWasmBackend>();

export function setFlowWasmBackend(b: FlowWasmBackend | null): void {
  _registry.set(b);
}

export function getFlowWasmBackend(): FlowWasmBackend | null {
  return _registry.get();
}
