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

// --- Double Metaphone ------------------------------------------------------

const DM_VOWELS = "AEIOUY";
const isDmVowel = (c: string): boolean => c.length === 1 && DM_VOWELS.includes(c);

/** JS `String.prototype.slice` over the char buffer -- negative indices from the
 * end, clamp to [0,len], start>=end -> "". Mirrors `phonetic.rs::dm_slice`. */
function dmSlice(chars: readonly string[], a: number, b: number): string {
  const len = chars.length;
  const start = a < 0 ? Math.max(len + a, 0) : Math.min(a, len);
  const end = b < 0 ? Math.max(len + b, 0) : Math.min(b, len);
  if (start >= end) return "";
  return chars.slice(start, end).join("");
}

function dmInitialGreekCh(norm: string, at: (i: number) => string): boolean {
  return (
    norm.startsWith("CHIA") ||
    norm.startsWith("CHEM") ||
    norm.startsWith("CHYM") ||
    norm.startsWith("CHARAC") ||
    norm.startsWith("CHARIS") ||
    (norm.startsWith("CHOR") && at(4) !== "E")
  );
}

function dmGreekCh(s: string): boolean {
  return s.includes("ORCHES") || s.includes("ARCHIT") || s.includes("ORCHID");
}

function dmInitialGForKj(s: string): boolean {
  const a = s[0] ?? "\0";
  const b = s[1] ?? "\0";
  return (
    a === "Y" ||
    (a === "E" && "BILPRSY".includes(b)) ||
    (a === "I" && "BELN".includes(b))
  );
}

function dmInitialAngerException(s: string): boolean {
  return s.length >= 6 && "DMR".includes(s[0] ?? "\0") && s.slice(1, 6) === "ANGER";
}

/** Double Metaphone (Lawrence Philips) -> [primary, secondary]. Byte-identical
 * to `goldenflow-core::phonetic::double_metaphone`. */
export function doubleMetaphoneTs(value: string): [string, string] {
  let primary = "";
  let secondary = "";
  const length = Array.from(value).length;
  const last = length - 1;
  const chars = Array.from(value.toUpperCase());
  chars.push(" ", " ", " ", " ", " ");
  const norm = chars.join("");

  const isSlavoGermanic =
    norm.includes("W") || norm.includes("K") || norm.includes("CZ") || norm.includes("WITZ");
  const isGermanic =
    norm.startsWith("VAN ") || norm.startsWith("VON ") || norm.startsWith("SCH");

  const at = (i: number): string => (i >= 0 && i < chars.length ? chars[i]! : "\0");
  const slice = (a: number, b: number): string => dmSlice(chars, a, b);

  let index = 0;

  if (
    norm.startsWith("GN") ||
    norm.startsWith("KN") ||
    norm.startsWith("PN") ||
    norm.startsWith("WR") ||
    norm.startsWith("PS")
  ) {
    index += 1;
  }

  if (at(0) === "X") {
    primary += "S";
    secondary += "S";
    index += 1;
  }

  while (index < length) {
    const previous = at(index - 1);
    const next = at(index + 1);
    const nextnext = at(index + 2);
    const c = at(index);

    if (c === "A" || c === "E" || c === "I" || c === "O" || c === "U" || c === "Y" || c === "À" || c === "Ê" || c === "É") {
      if (index === 0) {
        primary += "A";
        secondary += "A";
      }
      index += 1;
    } else if (c === "B") {
      primary += "P";
      secondary += "P";
      if (next === "B") index += 1;
      index += 1;
    } else if (c === "Ç") {
      primary += "S";
      secondary += "S";
      index += 1;
    } else if (c === "C") {
      if (
        previous === "A" &&
        next === "H" &&
        nextnext !== "I" &&
        !isDmVowel(at(index - 2)) &&
        (nextnext !== "E" || slice(index - 2, index + 4) === "BACHER" || slice(index - 2, index + 4) === "MACHER")
      ) {
        primary += "K";
        secondary += "K";
        index += 2;
      } else if (index === 0 && slice(index + 1, index + 6) === "AESAR") {
        primary += "S";
        secondary += "S";
        index += 2;
      } else if (slice(index + 1, index + 4) === "HIA") {
        primary += "K";
        secondary += "K";
        index += 2;
      } else if (next === "H") {
        if (index > 0 && nextnext === "A" && at(index + 3) === "E") {
          primary += "K";
          secondary += "X";
          index += 2;
        } else if (index === 0 && dmInitialGreekCh(norm, at)) {
          primary += "K";
          secondary += "K";
          index += 2;
        } else {
          if (
            isGermanic ||
            dmGreekCh(slice(index - 2, index + 4)) ||
            nextnext === "T" ||
            nextnext === "S" ||
            ((index === 0 || previous === "A" || previous === "E" || previous === "O" || previous === "U") &&
              " BFHLMNRVW".includes(nextnext))
          ) {
            primary += "K";
            secondary += "K";
          } else if (index === 0) {
            primary += "X";
            secondary += "X";
          } else if (slice(0, 2) === "MC") {
            primary += "K";
            secondary += "K";
          } else {
            primary += "X";
            secondary += "K";
          }
          index += 2;
        }
      } else if (next === "Z" && slice(index - 2, index) !== "WI") {
        primary += "S";
        secondary += "X";
        index += 2;
      } else if (slice(index + 1, index + 4) === "CIA") {
        primary += "X";
        secondary += "X";
        index += 3;
      } else if (next === "C" && !(index === 1 && at(0) === "M")) {
        if ((nextnext === "I" || nextnext === "E" || nextnext === "H") && slice(index + 2, index + 4) !== "HU") {
          const sv = slice(index - 1, index + 4);
          if ((index === 1 && previous === "A") || sv === "UCCEE" || sv === "UCCES") {
            primary += "KS";
            secondary += "KS";
          } else {
            primary += "X";
            secondary += "X";
          }
          index += 3;
        } else {
          primary += "K";
          secondary += "K";
          index += 2;
        }
      } else if (next === "G" || next === "K" || next === "Q") {
        primary += "K";
        secondary += "K";
        index += 2;
      } else if (next === "I" && (nextnext === "E" || nextnext === "O")) {
        primary += "S";
        secondary += "X";
        index += 2;
      } else if (next === "I" || next === "E" || next === "Y") {
        primary += "S";
        secondary += "S";
        index += 2;
      } else {
        primary += "K";
        secondary += "K";
        if (next === " " && (nextnext === "C" || nextnext === "G" || nextnext === "Q")) {
          index += 3;
        } else {
          index += 1;
        }
      }
    } else if (c === "D") {
      if (next === "G") {
        if (nextnext === "E" || nextnext === "I" || nextnext === "Y") {
          primary += "J";
          secondary += "J";
          index += 3;
        } else {
          primary += "TK";
          secondary += "TK";
          index += 2;
        }
      } else if (next === "T" || next === "D") {
        primary += "T";
        secondary += "T";
        index += 2;
      } else {
        primary += "T";
        secondary += "T";
        index += 1;
      }
    } else if (c === "F") {
      if (next === "F") index += 1;
      index += 1;
      primary += "F";
      secondary += "F";
    } else if (c === "G") {
      if (next === "H") {
        if (index > 0 && !isDmVowel(previous)) {
          primary += "K";
          secondary += "K";
          index += 2;
        } else if (index === 0) {
          if (nextnext === "I") {
            primary += "J";
            secondary += "J";
          } else {
            primary += "K";
            secondary += "K";
          }
          index += 2;
        } else if (
          "BHD".includes(at(index - 2)) ||
          "BHD".includes(at(index - 3)) ||
          "BH".includes(at(index - 4))
        ) {
          index += 2;
        } else {
          if (index > 2 && previous === "U" && "CGLRT".includes(at(index - 3))) {
            primary += "F";
            secondary += "F";
          } else if (index > 0 && previous !== "I") {
            primary += "K";
            secondary += "K";
          }
          index += 2;
        }
      } else if (next === "N") {
        if (index === 1 && isDmVowel(at(0)) && !isSlavoGermanic) {
          primary += "KN";
          secondary += "N";
        } else if (
          slice(index + 2, index + 4) !== "EY" &&
          slice(index + 1, chars.length) !== "Y" &&
          !isSlavoGermanic
        ) {
          primary += "N";
          secondary += "KN";
        } else {
          primary += "KN";
          secondary += "KN";
        }
        index += 2;
      } else if (slice(index + 1, index + 3) === "LI" && !isSlavoGermanic) {
        primary += "KL";
        secondary += "L";
        index += 2;
      } else if (
        (index === 0 && dmInitialGForKj(slice(1, 3))) ||
        (slice(index + 1, index + 3) === "ER" &&
          previous !== "I" &&
          previous !== "E" &&
          !dmInitialAngerException(slice(0, 6))) ||
        (next === "Y" && previous !== "E" && previous !== "G" && previous !== "I" && previous !== "R")
      ) {
        primary += "K";
        secondary += "J";
        index += 2;
      } else if (
        next === "E" ||
        next === "I" ||
        next === "Y" ||
        ((previous === "A" || previous === "O") && next === "G" && nextnext === "I")
      ) {
        if (slice(index + 1, index + 3) === "ET" || isGermanic) {
          primary += "K";
          secondary += "K";
        } else {
          primary += "J";
          secondary += slice(index + 1, index + 5) === "IER " ? "J" : "K";
        }
        index += 2;
      } else {
        if (next === "G") index += 1;
        index += 1;
        primary += "K";
        secondary += "K";
      }
    } else if (c === "H") {
      if (isDmVowel(next) && (index === 0 || isDmVowel(previous))) {
        primary += "H";
        secondary += "H";
        index += 1;
      }
      index += 1;
    } else if (c === "J") {
      if (slice(index, index + 4) === "JOSE" || slice(0, 4) === "SAN ") {
        if (slice(0, 4) === "SAN " || (index === 0 && at(index + 4) === " ")) {
          primary += "H";
          secondary += "H";
        } else {
          primary += "J";
          secondary += "H";
        }
        index += 1;
      } else {
        if (index === 0) {
          primary += "J";
          secondary += "A";
        } else if (!isSlavoGermanic && (next === "A" || next === "O") && isDmVowel(previous)) {
          primary += "J";
          secondary += "H";
        } else if (index === last) {
          primary += "J";
        } else if (
          previous !== "S" &&
          previous !== "K" &&
          previous !== "L" &&
          !(next !== "\0" && "LTKSNMBZ".includes(next))
        ) {
          primary += "J";
          secondary += "J";
        } else if (next === "J") {
          index += 1;
        }
        index += 1;
      }
    } else if (c === "K") {
      if (next === "K") index += 1;
      primary += "K";
      secondary += "K";
      index += 1;
    } else if (c === "L") {
      if (next === "L") {
        if (
          (index === length - 3 &&
            ((previous === "A" && nextnext === "E") ||
              (previous === "I" && (nextnext === "O" || nextnext === "A")))) ||
          (previous === "A" &&
            nextnext === "E" &&
            (at(last) === "A" ||
              at(last) === "O" ||
              slice(last - 1, length).includes("AS") ||
              slice(last - 1, length).includes("OS")))
        ) {
          primary += "L";
          index += 2;
        } else {
          index += 1;
          primary += "L";
          secondary += "L";
          index += 1;
        }
      } else {
        primary += "L";
        secondary += "L";
        index += 1;
      }
    } else if (c === "M") {
      if (
        next === "M" ||
        (previous === "U" && next === "B" && (index + 1 === last || slice(index + 2, index + 4) === "ER"))
      ) {
        index += 1;
      }
      index += 1;
      primary += "M";
      secondary += "M";
    } else if (c === "N") {
      if (next === "N") index += 1;
      index += 1;
      primary += "N";
      secondary += "N";
    } else if (c === "Ñ") {
      index += 1;
      primary += "N";
      secondary += "N";
    } else if (c === "P") {
      if (next === "H") {
        primary += "F";
        secondary += "F";
        index += 2;
      } else {
        if (next === "P" || next === "B") index += 1;
        index += 1;
        primary += "P";
        secondary += "P";
      }
    } else if (c === "Q") {
      if (next === "Q") index += 1;
      index += 1;
      primary += "K";
      secondary += "K";
    } else if (c === "R") {
      if (
        index === last &&
        !isSlavoGermanic &&
        previous === "E" &&
        at(index - 2) === "I" &&
        at(index - 4) !== "M" &&
        at(index - 3) !== "E" &&
        at(index - 3) !== "A"
      ) {
        secondary += "R";
      } else {
        primary += "R";
        secondary += "R";
      }
      if (next === "R") index += 1;
      index += 1;
    } else if (c === "S") {
      if (next === "L" && (previous === "I" || previous === "Y")) {
        index += 1;
      } else if (index === 0 && slice(1, 5) === "UGAR") {
        primary += "X";
        secondary += "S";
        index += 1;
      } else if (next === "H") {
        const s = slice(index + 1, index + 5);
        if (s.includes("EIM") || s.includes("OEK") || s.includes("OLM") || s.includes("OLZ")) {
          primary += "S";
          secondary += "S";
        } else {
          primary += "X";
          secondary += "X";
        }
        index += 2;
      } else if (next === "I" && (nextnext === "O" || nextnext === "A")) {
        if (isSlavoGermanic) {
          primary += "S";
          secondary += "S";
        } else {
          primary += "S";
          secondary += "X";
        }
        index += 3;
      } else if (next === "Z" || (index === 0 && (next === "L" || next === "M" || next === "N" || next === "W"))) {
        primary += "S";
        secondary += "X";
        if (next === "Z") index += 1;
        index += 1;
      } else if (next === "C") {
        if (nextnext === "H") {
          const sv = slice(index + 3, index + 5);
          if (
            (sv.startsWith("E") && "DMNR".includes(sv[1] ?? "")) ||
            sv === "UY" ||
            sv === "OO"
          ) {
            if (sv === "ER" || sv === "EN") {
              primary += "X";
              secondary += "SK";
            } else {
              primary += "SK";
              secondary += "SK";
            }
            index += 3;
          } else if (index === 0 && !isDmVowel(at(3)) && at(3) !== "W") {
            primary += "X";
            secondary += "S";
            index += 3;
          } else {
            primary += "X";
            secondary += "X";
            index += 3;
          }
        } else if (nextnext === "I" || nextnext === "E" || nextnext === "Y") {
          primary += "S";
          secondary += "S";
          index += 3;
        } else {
          primary += "SK";
          secondary += "SK";
          index += 3;
        }
      } else {
        const sv = slice(index - 2, index);
        if (index === last && (sv === "AI" || sv === "OI")) {
          secondary += "S";
        } else {
          primary += "S";
          secondary += "S";
        }
        if (next === "S") index += 1;
        index += 1;
      }
    } else if (c === "T") {
      if (
        (next === "I" && nextnext === "O" && at(index + 3) === "N") ||
        (next === "I" && nextnext === "A") ||
        (next === "C" && nextnext === "H")
      ) {
        primary += "X";
        secondary += "X";
        index += 3;
      } else if (next === "H" || (next === "T" && nextnext === "H")) {
        if (isGermanic || ((nextnext === "O" || nextnext === "A") && at(index + 3) === "M")) {
          primary += "T";
          secondary += "T";
        } else {
          primary += "0";
          secondary += "T";
        }
        index += 2;
      } else {
        if (next === "T" || next === "D") index += 1;
        index += 1;
        primary += "T";
        secondary += "T";
      }
    } else if (c === "V") {
      if (next === "V") index += 1;
      primary += "F";
      secondary += "F";
      index += 1;
    } else if (c === "W") {
      if (next === "R") {
        primary += "R";
        secondary += "R";
        index += 2;
      } else {
        if (index === 0) {
          if (isDmVowel(next)) {
            primary += "A";
            secondary += "F";
          } else if (next === "H") {
            primary += "A";
            secondary += "A";
          }
        }
        if (
          ((previous === "E" || previous === "O") &&
            next === "S" &&
            nextnext === "K" &&
            (at(index + 3) === "I" || at(index + 3) === "Y")) ||
          slice(0, 3) === "SCH" ||
          (index === last && isDmVowel(previous))
        ) {
          secondary += "F";
          index += 1;
        } else if (next === "I" && (nextnext === "C" || nextnext === "T") && at(index + 3) === "Z") {
          primary += "TS";
          secondary += "FX";
          index += 4;
        } else {
          index += 1;
        }
      }
    } else if (c === "X") {
      if (!(index === last && previous === "U" && (at(index - 2) === "A" || at(index - 2) === "O"))) {
        primary += "KS";
        secondary += "KS";
      }
      if (next === "C" || next === "X") index += 1;
      index += 1;
    } else if (c === "Z") {
      if (next === "H") {
        primary += "J";
        secondary += "J";
        index += 2;
      } else {
        if (
          (next === "Z" && (nextnext === "A" || nextnext === "I" || nextnext === "O")) ||
          (isSlavoGermanic && index > 0 && previous !== "T")
        ) {
          primary += "S";
          secondary += "TS";
        } else {
          primary += "S";
          secondary += "S";
        }
        if (next === "Z") index += 1;
        index += 1;
      }
    } else {
      index += 1;
    }
  }

  return [primary, secondary];
}

function doubleMetaphonePrimary(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => (v === null || typeof v !== "string" ? v : doubleMetaphoneTs(v)[0]));
}

function doubleMetaphoneAlt(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => (v === null || typeof v !== "string" ? v : doubleMetaphoneTs(v)[1]));
}

registerTransform(
  { name: "double_metaphone_primary", inputTypes: ["name", "string"], priority: 40, mode: "series" },
  doubleMetaphonePrimary,
);

registerTransform(
  { name: "double_metaphone_alt", inputTypes: ["name", "string"], priority: 40, mode: "series" },
  doubleMetaphoneAlt,
);
