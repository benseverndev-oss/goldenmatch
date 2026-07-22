import { describe, it, expect } from "vitest";
import {
  scoreField,
  scorePair,
  findExactMatches,
  findFuzzyMatches,
  jaro,
  jaroWinkler,
  levenshteinDistance,
  levenshteinSimilarity,
  tokenSortRatio,
  soundexMatch,
  diceCoefficient,
  jaccardSimilarity,
  phashSimilarity,
  radialSimilarity,
  audioFpSimilarity,
  initialismMatch,
  deriveInitialism,
  aliasMatch,
  ensembleScore,
  scoreMatrix,
  applyTransform,
} from "../../src/core/index.js";
import type { MatchkeyConfig, MatchkeyField, Row } from "../../src/core/index.js";
import { canonicalCompanyForm } from "../../src/core/refdata/business.js";
import { canonicalForm } from "../../src/core/refdata/givenNames.js";

describe("jaro / jaroWinkler", () => {
  it("jaro MARTHA ~= MARHTA matches Python (0.9444)", () => {
    expect(jaro("MARTHA", "MARHTA")).toBeCloseTo(0.9444, 4);
  });

  it("jaroWinkler MARTHA ~= MARHTA matches Python (0.9611)", () => {
    expect(jaroWinkler("MARTHA", "MARHTA")).toBeCloseTo(0.9611, 4);
  });

  it("jaroWinkler DIXON / DICKSONX matches Python (0.8133)", () => {
    expect(jaroWinkler("DIXON", "DICKSONX")).toBeCloseTo(0.8133, 4);
  });

  it("jaroWinkler JELLYFISH / SMELLYFISH matches Python (0.8963)", () => {
    expect(jaroWinkler("JELLYFISH", "SMELLYFISH")).toBeCloseTo(0.8963, 4);
  });

  it("jaro identical -> 1.0", () => {
    expect(jaro("hello", "hello")).toBe(1.0);
  });

  it("jaro empty -> 0", () => {
    expect(jaro("", "hello")).toBe(0.0);
  });
});

describe("levenshtein", () => {
  it("kitten -> sitting is distance 3", () => {
    expect(levenshteinDistance("kitten", "sitting")).toBe(3);
  });

  it("identical distance 0", () => {
    expect(levenshteinDistance("abc", "abc")).toBe(0);
  });

  it("empty -> len", () => {
    expect(levenshteinDistance("", "abc")).toBe(3);
  });

  it("similarity 1.0 for identical", () => {
    expect(levenshteinSimilarity("abc", "abc")).toBe(1.0);
  });

  it("kitten/sitting similarity matches Python (1 - 3/7 = 0.5714)", () => {
    expect(levenshteinSimilarity("kitten", "sitting")).toBeCloseTo(0.5714, 4);
  });

  it("saturday/sunday similarity matches Python (1 - 3/8 = 0.6250)", () => {
    expect(levenshteinSimilarity("saturday", "sunday")).toBeCloseTo(0.625, 4);
  });
});

describe("tokenSortRatio (rapidfuzz-compatible)", () => {
  it("John Smith / Smith John -> 1.0 (same token set)", () => {
    expect(tokenSortRatio("John Smith", "Smith John")).toBe(1.0);
  });

  it("New York Mets / Mets New York -> 1.0", () => {
    expect(tokenSortRatio("New York Mets", "Mets New York")).toBe(1.0);
  });

  it("John Smith / Smith Johnson matches Indel ratio (0.8696)", () => {
    // sorted: "john smith" (10) vs "johnson smith" (13)
    // indel distance = 3 (insert s, o, n), 1 - 3/23 = 20/23 ≈ 0.8696
    expect(tokenSortRatio("John Smith", "Smith Johnson")).toBeCloseTo(0.8696, 4);
  });

  it("lowercases before sorting (case-insensitive)", () => {
    expect(tokenSortRatio("John SMITH", "smith JOHN")).toBe(1.0);
  });

  it("strips punctuation (rapidfuzz preprocessing)", () => {
    expect(tokenSortRatio("John, Smith!", "smith john.")).toBe(1.0);
  });

  it("different tokens return < 1", () => {
    expect(tokenSortRatio("John", "Jane")).toBeLessThan(1.0);
  });
});

describe("soundexMatch", () => {
  it("Robert/Rupert same code -> 1.0 (both R163)", () => {
    expect(soundexMatch("Robert", "Rupert")).toBe(1.0);
  });

  it("Smith/Smyth same code -> 1.0 (both S530)", () => {
    expect(soundexMatch("Smith", "Smyth")).toBe(1.0);
  });

  it("Smith/Doe -> 0", () => {
    expect(soundexMatch("Smith", "Doe")).toBe(0.0);
  });
});

describe("ensembleScore", () => {
  it("identical strings -> 1", () => {
    expect(ensembleScore("hello", "hello")).toBe(1.0);
  });

  it("returns at least jaro_winkler", () => {
    const jw = jaroWinkler("Smith", "Smyth");
    const en = ensembleScore("Smith", "Smyth");
    expect(en).toBeGreaterThanOrEqual(jw);
  });
});

describe("dice / jaccard (bloom filter hex)", () => {
  it("dice of identical bloom filters -> 1.0", () => {
    const bloom = applyTransform("hello", "bloom_filter");
    expect(bloom).not.toBe(null);
    expect(diceCoefficient(bloom!, bloom!)).toBe(1.0);
  });

  it("jaccard of identical -> 1.0", () => {
    const bloom = applyTransform("hello", "bloom_filter");
    expect(jaccardSimilarity(bloom!, bloom!)).toBe(1.0);
  });

  it("all-zero filters -> 0", () => {
    // 256 bits / 8 = 32 bytes, so 64 hex chars
    const zero = "0".repeat(64);
    expect(diceCoefficient(zero, zero)).toBe(0.0);
    expect(jaccardSimilarity(zero, zero)).toBe(0.0);
  });

  // #784: different-length hex inputs must not crash and must zero-pad the
  // shorter filter to the longer length (parity with the Python single-pair
  // helpers, which were fixed to match their matrix variants). The fast-check
  // property suite only exercises same-length pairs, so these pin the
  // mismatched-length contract explicitly.
  it("mismatched lengths zero-pad rather than crash", () => {
    // "ff" (1 byte, 8 set bits) vs "ff00" (2 bytes, 8 set bits): the implicit
    // zero tail means |A|=|B|=8 and |A&B|=8 -> dice 1.0, jaccard 1.0.
    expect(diceCoefficient("ff", "ff00")).toBeCloseTo(1.0, 9);
    expect(jaccardSimilarity("ff", "ff00")).toBeCloseTo(1.0, 9);
    // Symmetric regardless of which argument is shorter.
    expect(diceCoefficient("ff00", "ff")).toBeCloseTo(1.0, 9);
    expect(jaccardSimilarity("ff00", "ff")).toBeCloseTo(1.0, 9);
  });

  it("mismatched lengths with partial overlap score in [0, 1]", () => {
    // "ff" = bits 0-7; "ff0f" = bits 0-7 and 12-15. Intersection 8, union 12.
    expect(diceCoefficient("ff", "ff0f")).toBeCloseTo((2 * 8) / (8 + 12), 9);
    expect(jaccardSimilarity("ff", "ff0f")).toBeCloseTo(8 / 12, 9);
  });

  it("mismatched all-zero lengths -> 0 (no crash)", () => {
    expect(diceCoefficient("0000", "000000")).toBe(0.0);
    expect(jaccardSimilarity("0000", "000000")).toBe(0.0);
  });
});

describe("phash (perceptual-hash hex similarity)", () => {
  it("identical -> 1.0", () => {
    expect(phashSimilarity("ffffffffffffffff", "ffffffffffffffff")).toBe(1.0);
  });

  it("all bits differ -> 0.0", () => {
    expect(phashSimilarity("0000000000000000", "ffffffffffffffff")).toBe(0.0);
  });

  it("one bit differs over 64 bits -> 63/64", () => {
    expect(phashSimilarity("ff00ff00ff00ff00", "ff00ff00ff00ff01")).toBe(63 / 64);
  });

  it("strips a 0x/0X prefix and left-pads odd length", () => {
    expect(phashSimilarity("0x1234", "1234")).toBe(1.0);
    expect(phashSimilarity("abc", "0abc")).toBe(1.0); // odd -> "0abc" both sides
  });

  it("mismatched lengths: every set bit in the longer's tail is a difference", () => {
    // "ff" (8 bits) vs "ffff" (16 bits): nbits=16, common byte matches, tail 0xff
    // contributes 8 differences -> 1 - 8/16 = 0.5.
    expect(phashSimilarity("ff", "ffff")).toBe(0.5);
    expect(phashSimilarity("ffff", "ff")).toBe(0.5); // symmetric
  });

  it("non-hex or empty -> 0.0", () => {
    expect(phashSimilarity("zzzz", "1234")).toBe(0.0);
    expect(phashSimilarity("", "")).toBe(0.0);
  });
});

describe("radial (rotation-aligned Pearson of hex radial profiles)", () => {
  it("identical profile -> 1.0", () => {
    expect(radialSimilarity("0a1b2c3d", "0a1b2c3d")).toBeCloseTo(1.0, 12);
  });

  it("a cyclic rotation aligns to 1.0", () => {
    // radial_align maxes Pearson over every cyclic shift, so a rotation of the
    // same profile finds a perfect alignment. "2c3d0a1b" and "3d0a1b2c" are the
    // shift-2 and shift-3 cyclic rotations of [0a,1b,2c,3d].
    expect(radialSimilarity("0a1b2c3d", "2c3d0a1b")).toBeCloseTo(1.0, 12);
    expect(radialSimilarity("0a1b2c3d", "3d0a1b2c")).toBeCloseTo(1.0, 12);
  });

  it("a constant profile has zero variance -> 0.0", () => {
    expect(radialSimilarity("0a0a0a0a", "01020304")).toBe(0.0);
  });

  it("mismatched-length / non-hex / empty -> 0.0", () => {
    expect(radialSimilarity("0a1b2c3d", "0a1b")).toBe(0.0); // length mismatch
    expect(radialSimilarity("zz", "0102")).toBe(0.0); // non-hex
    expect(radialSimilarity("", "")).toBe(0.0); // empty
  });

  it("signed-byte decode: 0x80..0xff map to negatives", () => {
    // Both sides identical still correlate to 1.0 regardless of sign.
    expect(radialSimilarity("7f80017e", "7f80017e")).toBeCloseTo(1.0, 12);
  });
});

describe("audio_fp (offset-aligned bit-error-rate of hex fingerprints)", () => {
  it("identical fingerprints -> 1.0", () => {
    expect(audioFpSimilarity("deadbeef", "deadbeef")).toBe(1.0);
    expect(audioFpSimilarity("deadbeefcafebabe", "deadbeefcafebabe")).toBe(1.0);
  });

  it("all bits differ over one word -> BER 1.0 -> 0.0", () => {
    expect(audioFpSimilarity("00000000", "ffffffff")).toBe(0.0);
  });

  it("one differing bit over 32 -> 1 - 1/32", () => {
    expect(audioFpSimilarity("00000001", "00000000")).toBe(1 - 1 / 32);
  });

  it("strips a 0x prefix", () => {
    expect(audioFpSimilarity("0xdeadbeef", "deadbeef")).toBe(1.0);
  });

  it("non-hex or empty -> 0.0", () => {
    expect(audioFpSimilarity("nothex00", "12345678")).toBe(0.0);
    expect(audioFpSimilarity("", "")).toBe(0.0); // empty -> BER 1.0 -> 0.0
  });
});

describe("initialism_match (business-name acronym matcher)", () => {
  it("derives an acronym, dropping legal-form tokens", () => {
    expect(deriveInitialism("International Business Machines Corp")).toBe("IBM");
    expect(deriveInitialism("General Electric Company")).toBe("GE");
    expect(deriveInitialism("Acme Industries LLC")).toBe("AI"); // LLC dropped
    expect(deriveInitialism("Apple Inc.")).toBe(""); // single lowercase token
    expect(deriveInitialism("3M Company")).toBe(""); // "3M" not alphabetic
    expect(deriveInitialism("NASA")).toBe("NASA"); // single acronym passes through
  });

  it("matches a name against its initialism (either direction), else 0.0", () => {
    // Values pinned against Python `_initialism_match_single`.
    expect(initialismMatch("International Business Machines Corp", "IBM")).toBe(1.0);
    expect(initialismMatch("IBM", "International Business Machines Corp")).toBe(1.0);
    expect(initialismMatch("General Electric Company", "GE")).toBe(1.0);
    expect(initialismMatch("Hewlett Packard", "HP")).toBe(1.0);
    expect(initialismMatch("IBM", "IBM")).toBe(1.0);
    expect(initialismMatch("Acme", "Acme")).toBe(0.0);
    expect(initialismMatch("Apple Inc.", "AI")).toBe(0.0);
    // Stopwords are NOT dropped, so these derive extra initials and miss.
    expect(initialismMatch("National Aeronautics and Space Administration", "NASA")).toBe(0.0);
    expect(initialismMatch("AT and T", "ATT")).toBe(0.0);
    expect(initialismMatch("", "IBM")).toBe(0.0);
  });
});

describe("alias_match (business + given-name canonical equality)", () => {
  it("canonicalizes a company name (legal-form strip + alias map)", () => {
    // Pinned against Python `refdata.business_aliases.canonical_company_form`.
    expect(canonicalCompanyForm("Acme Inc")).toBe("acme");
    expect(canonicalCompanyForm("Acme Incorporated")).toBe("acme");
    expect(canonicalCompanyForm("Acme Holdings Inc.")).toBe("acme"); // compound suffix peels
    expect(canonicalCompanyForm("Acme Limited Liability Company")).toBe("acme");
    expect(canonicalCompanyForm("Acme-Inc")).toBe("acme"); // hyphen is a leading separator
    expect(canonicalCompanyForm("Acme Group")).toBe("acme"); // descriptor variant stripped
    expect(canonicalCompanyForm("Google LLC")).toBe("alphabet"); // surface -> canonical
    expect(canonicalCompanyForm("International Business Machines Corp")).toBe(
      "international business machines",
    );
    expect(canonicalCompanyForm("Globex")).toBe("globex"); // OOV -> normalized passthrough
    expect(canonicalCompanyForm("Inc")).toBe("inc"); // no leading sep -> nothing stripped
    expect(canonicalCompanyForm("")).toBe("");
    expect(canonicalCompanyForm(null)).toBeNull();
  });

  it("canonicalizes a given name (lex-first nickname resolution)", () => {
    // Pinned against Python `refdata.given_names.canonical_form`.
    expect(canonicalForm("Bob")).toBe("robert");
    expect(canonicalForm("Robert")).toBe("robert");
    expect(canonicalForm("Kate")).toBe("catherine"); // lex-first across canonicals
    expect(canonicalForm("Xander")).toBe("alexander");
    expect(canonicalForm("Zzz")).toBe("zzz"); // OOV passthrough
  });

  it("matches on a shared non-empty business OR given canonical, else 0.0", () => {
    // Pinned against Python `_alias_match_single`.
    expect(aliasMatch("Acme Inc", "Acme Incorporated")).toBe(1.0);
    expect(aliasMatch("Google", "Alphabet Inc.")).toBe(1.0);
    expect(aliasMatch("IBM", "International Business Machines")).toBe(1.0);
    expect(aliasMatch("FedEx", "Federal Express")).toBe(1.0);
    expect(aliasMatch("Acme Group", "Acme")).toBe(1.0);
    expect(aliasMatch("Bob", "Robert")).toBe(1.0); // given-name half
    expect(aliasMatch("Kate", "Catherine")).toBe(1.0);
    expect(aliasMatch("Acme", "Globex")).toBe(0.0); // different OOV companies
    expect(aliasMatch("William", "Walter")).toBe(0.0); // unrelated given names
    expect(aliasMatch("", "")).toBe(0.0); // empty canonical never matches
  });
});

describe("scoreField", () => {
  it("exact a==a -> 1.0", () => {
    expect(scoreField("a", "a", "exact")).toBe(1.0);
  });

  it("exact a!=b -> 0.0", () => {
    expect(scoreField("a", "b", "exact")).toBe(0.0);
  });

  it("returns null if either input is null", () => {
    expect(scoreField(null, "a", "exact")).toBe(null);
    expect(scoreField("a", null, "jaro_winkler")).toBe(null);
    expect(scoreField(null, null, "exact")).toBe(null);
  });

  it("unknown scorer throws", () => {
    expect(() => scoreField("a", "b", "fake_scorer")).toThrow();
  });

  it("jaro_winkler returns similarity", () => {
    const s = scoreField("abc", "abc", "jaro_winkler");
    expect(s).toBe(1.0);
  });

  it("levenshtein", () => {
    const s = scoreField("abc", "abc", "levenshtein");
    expect(s).toBe(1.0);
  });

  it("token_sort", () => {
    const s = scoreField("a b", "b a", "token_sort");
    expect(s).toBe(1.0);
  });

  it("token_sort strips punctuation and lowercases (rapidfuzz parity)", () => {
    // "John, Smith!" vs "smith john." → both sort to "john smith" → 1.0
    expect(scoreField("John, Smith!", "smith john.", "token_sort")).toBe(1.0);
  });
});

describe("scorePair - weighted fields", () => {
  it("weighted aggregation of fields", () => {
    const rowA: Row = { name: "John", city: "NYC" };
    const rowB: Row = { name: "John", city: "NYC" };
    const fields: MatchkeyField[] = [
      { field: "name", transforms: [], scorer: "jaro_winkler", weight: 1.0 },
      { field: "city", transforms: [], scorer: "exact", weight: 1.0 },
    ];
    expect(scorePair(rowA, rowB, fields)).toBe(1.0);
  });

  it("returns 0 when weightSum=0 (all fields null)", () => {
    const rowA: Row = { name: null };
    const rowB: Row = { name: null };
    const fields: MatchkeyField[] = [
      { field: "name", transforms: [], scorer: "jaro_winkler", weight: 1.0 },
    ];
    expect(scorePair(rowA, rowB, fields)).toBe(0);
  });

  it("weighted average of partial matches", () => {
    const rowA: Row = { name: "John", city: "NYC" };
    const rowB: Row = { name: "John", city: "LA" };
    const fields: MatchkeyField[] = [
      { field: "name", transforms: [], scorer: "exact", weight: 1.0 },
      { field: "city", transforms: [], scorer: "exact", weight: 1.0 },
    ];
    // (1.0 * 1 + 0.0 * 1) / 2 = 0.5
    expect(scorePair(rowA, rowB, fields)).toBe(0.5);
  });
});

describe("findExactMatches", () => {
  it("groups by matchkey column", () => {
    const rows: Row[] = [
      { __row_id__: 0, email: "a@x.com" },
      { __row_id__: 1, email: "a@x.com" },
      { __row_id__: 2, email: "b@x.com" },
    ];
    const mk: MatchkeyConfig = {
      name: "email",
      type: "exact",
      fields: [{ field: "email", transforms: [], scorer: "exact", weight: 1.0 }],
    };
    const pairs = findExactMatches(rows, mk);
    expect(pairs.length).toBe(1);
    expect(pairs[0]!.idA).toBe(0);
    expect(pairs[0]!.idB).toBe(1);
    expect(pairs[0]!.score).toBe(1.0);
  });

  it("returns empty for 0 or 1 rows", () => {
    const mk: MatchkeyConfig = {
      name: "email",
      type: "exact",
      fields: [{ field: "email", transforms: [], scorer: "exact", weight: 1.0 }],
    };
    expect(findExactMatches([], mk)).toEqual([]);
    expect(findExactMatches([{ __row_id__: 0, email: "a" }], mk)).toEqual([]);
  });

  it("skips rows where matchkey field is null", () => {
    const rows: Row[] = [
      { __row_id__: 0, email: null },
      { __row_id__: 1, email: null },
      { __row_id__: 2, email: "x@x.com" },
    ];
    const mk: MatchkeyConfig = {
      name: "email",
      type: "exact",
      fields: [{ field: "email", transforms: [], scorer: "exact", weight: 1.0 }],
    };
    const pairs = findExactMatches(rows, mk);
    expect(pairs.length).toBe(0);
  });
});

describe("findFuzzyMatches", () => {
  it("NxN scoring within block", () => {
    const rows: Row[] = [
      { __row_id__: 0, name: "Jon Smith" },
      { __row_id__: 1, name: "John Smith" },
      { __row_id__: 2, name: "Zeke Xavier" },
    ];
    const mk: MatchkeyConfig = {
      name: "name_fuzzy",
      type: "weighted",
      threshold: 0.7,
      fields: [{ field: "name", transforms: [], scorer: "jaro_winkler", weight: 1.0 }],
    };
    const pairs = findFuzzyMatches(rows, mk);
    // Jon/John should match; Zeke should not match either
    const hasPair01 = pairs.some((p) => p.idA === 0 && p.idB === 1);
    expect(hasPair01).toBe(true);
  });

  it("empty if fewer than 2 rows", () => {
    const mk: MatchkeyConfig = {
      name: "f",
      type: "weighted",
      threshold: 0.85,
      fields: [{ field: "name", transforms: [], scorer: "jaro_winkler", weight: 1.0 }],
    };
    expect(findFuzzyMatches([], mk)).toEqual([]);
  });
});

describe("scoreMatrix", () => {
  it("symmetric with 0 diagonal", () => {
    const m = scoreMatrix(["abc", "abd", "xyz"], "jaro_winkler");
    expect(m.length).toBe(3);
    expect(m[0]![0]).toBe(0); // diagonal
    expect(m[0]![1]).toBe(m[1]![0]); // symmetric
  });
});
