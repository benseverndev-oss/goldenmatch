/**
 * loader.ts — universal WASM byte loader + instantiation for the goldenflow
 * identifier kernel. Edge-safe: the only node:* touch is the guarded dynamic
 * `import("node:fs/promises" as string)` idiom inside the shared runtime's
 * `resolveWasmBytes`, not here.
 *
 * Resolution order (delegated to `goldenmatch-wasm-runtime`): explicit bytes
 * -> explicit base64 -> explicit URL -> fs (Node) -> fetch (browser/Workers/
 * bundler). Any failure throws; index.ts turns that into the pure-TS
 * fallback (or rethrows under { require: true }).
 */
import {
  resolveWasmBytes as sharedResolveWasmBytes,
  type LoadOptions,
} from "goldenmatch-wasm-runtime";
import type { FlowWasmBackend } from "./backend.js";

export type { LoadOptions };

/**
 * Resolve the raw wasm bytes, pinning goldenflow's own artifact URL (computed
 * here so `import.meta.url` resolves to THIS package's own dist — passing the
 * URL through the shared runtime would resolve to the wrong location).
 */
export function resolveWasmBytes(opts: LoadOptions): Promise<Uint8Array> {
  return sharedResolveWasmBytes(
    opts,
    new URL("./artifacts/goldenflow_wasm_bg.wasm", import.meta.url),
  );
}

/**
 * Instantiate the goldenflow-wasm module and adapt it to a FlowWasmBackend.
 * Uses the wasm-bindgen `--target web` glue: the default export is the async
 * `init`, which accepts `{ module_or_path: <bytes|url|module> }`.
 */
export async function instantiateBackend(bytes: Uint8Array): Promise<FlowWasmBackend> {
  // Dynamic import of the generated glue (absent in a default checkout).
  const glue = (await import("./artifacts/goldenflow_wasm.js" as string)) as {
    // module_or_path accepts more (URL/Response/Module), but we only ever pass
    // the resolved bytes; typing it as Uint8Array avoids the DOM
    // `BufferSource` lib type (this package typechecks without the DOM lib).
    default: (input: { module_or_path: Uint8Array }) => Promise<unknown>;
    cc_validate: (s: string) => boolean;
    cc_format: (s: string) => string | undefined;
    cc_mask: (s: string) => string | undefined;
    iban_validate: (s: string) => boolean;
    iban_format: (s: string) => string | undefined;
    isbn_validate: (s: string) => boolean;
    isbn_normalize: (s: string) => string | undefined;
    ean_validate: (s: string) => boolean;
    vat_validate: (s: string) => boolean;
    vat_format: (s: string) => string | undefined;
    swift_validate: (s: string) => boolean;
    swift_format: (s: string) => string | undefined;
    aba_validate: (s: string) => boolean;
    imei_validate: (s: string) => boolean;
    isin_validate: (s: string) => boolean;
    cusip_validate: (s: string) => boolean;
    npi_validate: (s: string) => boolean;
    luhn_validate: (s: string) => boolean;
    cc_brand: (s: string) => string | undefined;
    name_transliterate: (s: string) => string;
    name_script: (s: string) => string;
    name_initials: (s: string) => string;
    strip_middle: (s: string) => string;
    strip_titles: (s: string) => string;
    strip_suffixes: (s: string) => string;
    name_proper: (s: string) => string;
    nickname_standardize: (s: string) => string;
    has_initial: (s: string) => boolean;
    split_name: (s: string) => string[];
    split_name_reverse: (s: string) => string[];
    merge_name: (first: string | undefined, last: string | undefined) => string | undefined;
    ssn_format: (s: string) => string;
    ssn_mask: (s: string) => string;
    ein_format: (s: string) => string;
    phone_digits: (s: string) => string;
    company_normalize: (s: string) => string | undefined;
    company_strip_legal: (s: string) => string | undefined;
    company_extract_legal: (s: string) => string | undefined;
    email_lowercase: (s: string) => string;
    email_normalize: (s: string) => string;
    email_canonical: (s: string) => string;
    email_mask: (s: string) => string | undefined;
    email_extract_domain: (s: string) => string | undefined;
    email_validate: (s: string) => boolean | undefined;
    soundex: (s: string) => string;
    double_metaphone_primary: (s: string) => string;
    double_metaphone_alt: (s: string) => string;
    url_normalize: (s: string) => string | undefined;
    url_extract_domain: (s: string) => string | undefined;
    url_strip_tracking: (s: string) => string | undefined;
    url_strip_www: (s: string) => string | undefined;
    url_canonical: (s: string) => string | undefined;
    currency_strip: (s: string) => number | undefined;
    percentage_normalize: (s: string) => number | undefined;
    to_integer: (s: string) => number | undefined;
    comma_decimal: (s: string) => number | undefined;
    scientific_to_decimal: (s: string) => number | undefined;
    fraction_to_decimal: (s: string) => number | undefined;
    roman_to_int: (s: string) => number | undefined;
    ordinal_to_int: (s: string) => number | undefined;
    round_value: (x: number, n: number) => number;
    clamp_value: (x: number, minVal: number, maxVal: number) => number;
    abs_value: (x: number) => number;
    fill_zero: (x: number | undefined) => number;
    boolean_normalize: (s: string) => boolean | undefined;
    gender_standardize: (s: string) => string;
    null_standardize: (s: string) => string | undefined;
    category_normalize_key: (s: string) => string;
    address_standardize: (s: string) => string;
    address_expand: (s: string) => string;
    state_abbreviate: (s: string) => string;
    state_expand: (s: string) => string;
    zip_normalize: (s: string) => string;
    country_standardize: (s: string) => string;
    unit_normalize: (s: string) => string;
    split_address: (s: string) => (string | null)[];
    strip: (s: string) => string;
    collapse_whitespace: (s: string) => string;
    normalize_quotes: (s: string) => string;
    normalize_line_endings: (s: string) => string;
    remove_html_tags: (s: string) => string;
    remove_urls: (s: string) => string;
    remove_digits: (s: string) => string;
    remove_punctuation: (s: string) => string;
    remove_emojis: (s: string) => string;
    extract_numbers: (s: string) => string;
    truncate: (s: string, n: number) => string;
    pad_left: (s: string, width: number, pad: string) => string;
    pad_right: (s: string, width: number, pad: string) => string;
    lowercase: (s: string) => string;
    uppercase: (s: string) => string;
    title_case: (s: string) => string;
    normalize_unicode: (s: string) => string;
    fix_mojibake: (s: string) => string;
    fuzz_ratio: (a: string, b: string) => number;
    // wasm-bindgen `Vec<i32>` maps to `Int32Array`; `Vec<String>` in/out map to
    // `string[]`.
    build_canonical_map: (
      values: string[],
      counts: Int32Array,
      freqThreshold: number,
      matchThreshold: number,
    ) => string[];
    // Fused chain: `Vec<String>` in, a `ChainOut` class out (wasm-bindgen getters
    // `values` -> `string[]`, `changed` -> `Uint32Array`).
    apply_chain: (
      values: string[],
      names: string[],
    ) => { values: string[]; changed: Uint32Array };
    fusable_kernel_names: () => string[];
    // Auto-detect type inference: `(string | null)[]` sample + hint -> bare type.
    // wasm-bindgen `Vec<JsValue>` maps to a JS array; the TS caller passes
    // trimmed non-null strings.
    infer_type: (values: (string | null)[], hint: string) => string;
  };
  await glue.default({ module_or_path: bytes });

  return {
    ccValidate: (s) => glue.cc_validate(s),
    ccFormat: (s) => glue.cc_format(s),
    ccMask: (s) => glue.cc_mask(s),
    ibanValidate: (s) => glue.iban_validate(s),
    ibanFormat: (s) => glue.iban_format(s),
    isbnValidate: (s) => glue.isbn_validate(s),
    isbnNormalize: (s) => glue.isbn_normalize(s),
    eanValidate: (s) => glue.ean_validate(s),
    vatValidate: (s) => glue.vat_validate(s),
    vatFormat: (s) => glue.vat_format(s),
    swiftValidate: (s) => glue.swift_validate(s),
    swiftFormat: (s) => glue.swift_format(s),
    abaValidate: (s) => glue.aba_validate(s),
    imeiValidate: (s) => glue.imei_validate(s),
    isinValidate: (s) => glue.isin_validate(s),
    cusipValidate: (s) => glue.cusip_validate(s),
    npiValidate: (s) => glue.npi_validate(s),
    luhnValidate: (s) => glue.luhn_validate(s),
    ccBrand: (s) => glue.cc_brand(s),
    nameTransliterate: (s) => glue.name_transliterate(s),
    nameScript: (s) => glue.name_script(s),
    nameInitials: (s) => glue.name_initials(s),
    stripMiddle: (s) => glue.strip_middle(s),
    stripTitles: (s) => glue.strip_titles(s),
    stripSuffixes: (s) => glue.strip_suffixes(s),
    nameProper: (s) => glue.name_proper(s),
    nicknameStandardize: (s) => glue.nickname_standardize(s),
    hasInitial: (s) => glue.has_initial(s),
    splitName: (s) => glue.split_name(s),
    splitNameReverse: (s) => glue.split_name_reverse(s),
    mergeName: (first, last) => glue.merge_name(first ?? undefined, last ?? undefined),
    ssnFormat: (s) => glue.ssn_format(s),
    ssnMask: (s) => glue.ssn_mask(s),
    einFormat: (s) => glue.ein_format(s),
    phoneDigits: (s) => glue.phone_digits(s),
    companyNormalize: (s) => glue.company_normalize(s),
    companyStripLegal: (s) => glue.company_strip_legal(s),
    companyExtractLegal: (s) => glue.company_extract_legal(s),
    emailLowercase: (s) => glue.email_lowercase(s),
    emailNormalize: (s) => glue.email_normalize(s),
    emailCanonical: (s) => glue.email_canonical(s),
    emailMask: (s) => glue.email_mask(s),
    emailExtractDomain: (s) => glue.email_extract_domain(s),
    emailValidate: (s) => glue.email_validate(s) ?? false,
    soundex: (s) => glue.soundex(s),
    doubleMetaphonePrimary: (s) => glue.double_metaphone_primary(s),
    doubleMetaphoneAlt: (s) => glue.double_metaphone_alt(s),
    urlNormalize: (s) => glue.url_normalize(s),
    urlExtractDomain: (s) => glue.url_extract_domain(s),
    urlStripTracking: (s) => glue.url_strip_tracking(s),
    urlStripWww: (s) => glue.url_strip_www(s),
    urlCanonical: (s) => glue.url_canonical(s),
    currencyStrip: (s) => glue.currency_strip(s),
    percentageNormalize: (s) => glue.percentage_normalize(s),
    toInteger: (s) => glue.to_integer(s),
    fractionToDecimal: (s) => glue.fraction_to_decimal(s),
    romanToInt: (s) => glue.roman_to_int(s),
    ordinalToInt: (s) => glue.ordinal_to_int(s),
    commaDecimal: (s) => glue.comma_decimal(s),
    scientificToDecimal: (s) => glue.scientific_to_decimal(s),
    roundValue: (x, n) => glue.round_value(x, n),
    clampValue: (x, minVal, maxVal) => glue.clamp_value(x, minVal, maxVal),
    absValue: (x) => glue.abs_value(x),
    fillZero: (x) => glue.fill_zero(x),
    booleanNormalize: (s) => glue.boolean_normalize(s),
    genderStandardize: (s) => glue.gender_standardize(s),
    nullStandardize: (s) => glue.null_standardize(s),
    categoryNormalizeKey: (s) => glue.category_normalize_key(s),
    addressStandardize: (s) => glue.address_standardize(s),
    addressExpand: (s) => glue.address_expand(s),
    stateAbbreviate: (s) => glue.state_abbreviate(s),
    stateExpand: (s) => glue.state_expand(s),
    zipNormalize: (s) => glue.zip_normalize(s),
    countryStandardize: (s) => glue.country_standardize(s),
    unitNormalize: (s) => glue.unit_normalize(s),
    splitAddress: (s) => glue.split_address(s),
    strip: (s) => glue.strip(s),
    collapseWhitespace: (s) => glue.collapse_whitespace(s),
    normalizeQuotes: (s) => glue.normalize_quotes(s),
    normalizeLineEndings: (s) => glue.normalize_line_endings(s),
    removeHtmlTags: (s) => glue.remove_html_tags(s),
    removeUrls: (s) => glue.remove_urls(s),
    removeDigits: (s) => glue.remove_digits(s),
    removePunctuation: (s) => glue.remove_punctuation(s),
    removeEmojis: (s) => glue.remove_emojis(s),
    extractNumbers: (s) => glue.extract_numbers(s),
    truncate: (s, n) => glue.truncate(s, n),
    padLeft: (s, width, pad) => glue.pad_left(s, width, pad),
    padRight: (s, width, pad) => glue.pad_right(s, width, pad),
    lowercase: (s) => glue.lowercase(s),
    uppercase: (s) => glue.uppercase(s),
    titleCase: (s) => glue.title_case(s),
    normalizeUnicode: (s) => glue.normalize_unicode(s),
    fixMojibake: (s) => glue.fix_mojibake(s),
    fuzzRatio: (a, b) => glue.fuzz_ratio(a, b),
    buildCanonicalMap: (values, counts, freqThreshold, matchThreshold) =>
      glue.build_canonical_map(values, Int32Array.from(counts), freqThreshold, matchThreshold),
    applyChain: (values, names) => {
      const out = glue.apply_chain(values, names);
      return { values: out.values, changed: Array.from(out.changed) };
    },
    fusableKernelNames: () => glue.fusable_kernel_names(),
    inferType: (values, hint) => glue.infer_type(values, hint),
  };
}
