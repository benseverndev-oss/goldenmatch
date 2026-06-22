/**
 * Tests for the pure scalar canonicalizers (#1128).
 *
 * The input→output pairs are copied verbatim from the Python reference tests
 * (`packages/python/goldenflow/tests/test_canonicalize.py`), so this file
 * doubles as the byte-for-byte parity fixture: if the TS port ever diverges
 * from the documented spec, one of these breaks.
 */

import { describe, it, expect } from "vitest";
import { canonicalize } from "../../src/index.js";
import type { CanonicalizeKind } from "../../src/index.js";

const KINDS: CanonicalizeKind[] = ["email", "phone", "name", "postal"];

describe("canonicalize: email", () => {
  it.each([
    ["Alice@Example.COM", "alice@example.com"],
    ["  bob@x.org  ", "bob@x.org"],
    ["\tCarol@Y.io\n", "carol@y.io"],
    ["already@clean.com", "already@clean.com"],
    ["", ""],
  ])("%j -> %j", (raw, expected) => {
    expect(canonicalize(raw, "email")).toBe(expected);
  });
});

describe("canonicalize: phone", () => {
  it.each([
    ["(555) 123-4567", "5551234567"],
    ["+1 555 123 4567", "5551234567"], // NANP country code stripped
    ["1-555-123-4567", "5551234567"],
    ["15551234567", "5551234567"], // bare 11-digit leading 1
    ["555.123.4567", "5551234567"],
    ["+44 20 7946 0958", "442079460958"], // not 11 digits -> no strip
    ["12345678901", "2345678901"], // 11 digits, leading 1 -> strip
    ["1234567890", "1234567890"], // 10 digits, leading 1 -> KEEP
    ["phone: n/a", ""],
    ["", ""],
  ])("%j -> %j", (raw, expected) => {
    expect(canonicalize(raw, "phone")).toBe(expected);
  });
});

describe("canonicalize: name", () => {
  it.each([
    ["  John   SMITH ", "john smith"],
    ["O'Brien", "obrien"], // punctuation deleted
    ["Smith-Jones", "smithjones"],
    ["Dr. Jane Doe, Jr.", "dr jane doe jr"],
    ["María José", "maría josé"], // non-ASCII passes through
    ["", ""],
  ])("%j -> %j", (raw, expected) => {
    expect(canonicalize(raw, "name")).toBe(expected);
  });
});

describe("canonicalize: postal", () => {
  it.each([
    ["12345", "12345"],
    ["12345-6789", "12345"], // ZIP+4 -> first 5
    ["90210 ", "90210"],
    ["1234", "1234"], // fewer than 5 digits -> as-is
    ["SW1A 1AA", "SW1A1AA"], // UK: alnum-upper fallback
    ["k1a 0b1", "K1A0B1"], // CA: lowercased -> upper
    ["", ""],
  ])("%j -> %j", (raw, expected) => {
    expect(canonicalize(raw, "postal")).toBe(expected);
  });
});

describe("canonicalize: cross-cutting guarantees", () => {
  const CORPUS = [
    "Alice@Example.COM",
    "  bob@x.org  ",
    "(555) 123-4567",
    "+1 555 123 4567",
    "O'Brien",
    "Dr. Jane Doe, Jr.",
    "María José",
    "12345-6789",
    "SW1A 1AA",
    "k1a 0b1",
    "",
    "   ",
    "!!!",
    "MixedCASE 123 -- text",
  ];

  for (const kind of KINDS) {
    for (const raw of CORPUS) {
      it(`is idempotent: ${kind}(${JSON.stringify(raw)})`, () => {
        const once = canonicalize(raw, kind);
        expect(canonicalize(once, kind)).toBe(once);
      });
    }

    it(`maps null/undefined to "" for ${kind}`, () => {
      expect(canonicalize(null, kind)).toBe("");
      expect(canonicalize(undefined, kind)).toBe("");
    });
  }

  it("case folding is ASCII-only, not locale-aware", () => {
    // Locale-aware toLowerCase() folds the non-ASCII capitals (Turkish 'İ' ->
    // 'i̇'); ASCII-only must lowercase ONLY A-Z and leave the rest byte-identical.
    expect(canonicalize("İSTANBUL", "name")).toBe("İstanbul"); // 'İ' untouched
    expect(canonicalize("ÉCLAIR", "name")).toBe("Éclair"); // 'É' untouched
  });

  it("throws on an unknown kind", () => {
    expect(() => canonicalize("x", "zipcode" as CanonicalizeKind)).toThrow();
  });
});
