/**
 * Phonetic transforms -- ported from goldenflow/transforms/phonetic.py.
 * Side-effect module: registers phonetic transforms on import.
 *
 * Pure-TS reference for goldenflow-core's `phonetic` kernels; MUST reproduce
 * the Rust/Python bytes (byte-parity corpus).
 */

import type { ColumnValue } from "../types.js";
import { registerTransform } from "./registry.js";

// Soundex consonant classes; vowels + H/W/Y (and anything unmapped) code to "0".
const SOUNDEX_DIGIT: Record<string, string> = {
  B: "1", F: "1", P: "1", V: "1",
  C: "2", G: "2", J: "2", K: "2", Q: "2", S: "2", X: "2", Z: "2",
  D: "3", T: "3",
  L: "4",
  M: "5", N: "5",
  R: "6",
};

/** American Soundex (NARA). Leading letter + 3 digits; h/w transparent, vowels
 * reset the run; ASCII letters only; no letters -> "". */
export function soundexTs(val: string): string {
  const letters: string[] = [];
  for (const ch of val) {
    if (ch.length === 1 && ((ch >= "A" && ch <= "Z") || (ch >= "a" && ch <= "z"))) {
      letters.push(ch.toUpperCase());
    }
  }
  if (letters.length === 0) return "";
  let code = letters[0]!;
  let last = SOUNDEX_DIGIT[letters[0]!] ?? "0";
  for (let i = 1; i < letters.length; i++) {
    if (code.length >= 4) break;
    const c = letters[i]!;
    const d = SOUNDEX_DIGIT[c] ?? "0";
    if (d !== "0") {
      if (d !== last) code += d;
      last = d;
    } else if (c !== "H" && c !== "W") {
      last = "0";
    }
  }
  return (code + "000").slice(0, 4);
}

function soundex(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => (v === null || typeof v !== "string" ? v : soundexTs(v)));
}

registerTransform(
  { name: "soundex", inputTypes: ["name", "string"], priority: 40, mode: "series" },
  soundex,
);
