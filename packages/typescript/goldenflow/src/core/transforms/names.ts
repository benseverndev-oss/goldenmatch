/**
 * Name transforms — ported from goldenflow/transforms/names.py
 * Side-effect module: registers 8 name transforms on import.
 */

import type { ColumnValue, Row } from "../types.js";
import { registerTransform } from "./registry.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function mapStrings(
  values: readonly ColumnValue[],
  fn: (s: string) => string,
): ColumnValue[] {
  return values.map((v) => {
    if (v === null || typeof v !== "string") return v;
    return fn(v);
  });
}

const _TITLES = /^(Mr\.?|Mrs\.?|Ms\.?|Miss\.?|Dr\.?|Prof\.?|Rev\.?|Sr\.?|Sra\.?)\s+/i;
const _SUFFIXES = /\s+(Jr\.?|Sr\.?|II|III|IV|MD|PhD|PharmD|DDS|DVM|Esq\.?|CPA|RN|DO)$/i;
const _INITIAL_PATTERN = /\b[A-Z]\.\s/;
const _MC_PATTERN = /\bMc(\w)/g;
const _O_PATTERN = /\bO'(\w)/g;

// ---------------------------------------------------------------------------
// Nickname map
// ---------------------------------------------------------------------------

const _NICKNAMES: Record<string, string> = {
  bob: "Robert", rob: "Robert", robby: "Robert", robbie: "Robert", bobby: "Robert",
  bill: "William", billy: "William", will: "William", willy: "William",
  jim: "James", jimmy: "James", jamie: "James",
  mike: "Michael", mikey: "Michael", mick: "Michael",
  dick: "Richard", rick: "Richard", rich: "Richard", ricky: "Richard",
  tom: "Thomas", tommy: "Thomas",
  joe: "Joseph", joey: "Joseph",
  jack: "John", johnny: "John", jon: "Jonathan",
  dave: "David", davy: "David",
  steve: "Steven", stevie: "Steven",
  dan: "Daniel", danny: "Daniel",
  pat: "Patrick", patty: "Patricia", patsy: "Patricia",
  chris: "Christopher", kit: "Christopher",
  tony: "Anthony",
  ed: "Edward", eddie: "Edward", ted: "Edward", teddy: "Edward",
  al: "Albert", bert: "Albert",
  charlie: "Charles", chuck: "Charles",
  sam: "Samuel", sammy: "Samuel",
  ben: "Benjamin", benny: "Benjamin",
  matt: "Matthew",
  andy: "Andrew", drew: "Andrew",
  nick: "Nicholas",
  alex: "Alexander",
  liz: "Elizabeth", beth: "Elizabeth", betty: "Elizabeth",
  kate: "Katherine", kathy: "Katherine", katie: "Katherine",
  sue: "Susan", susie: "Susan",
  meg: "Margaret", maggie: "Margaret", peggy: "Margaret",
  jenny: "Jennifer", jen: "Jennifer",
  debbie: "Deborah", deb: "Deborah",
  barb: "Barbara",
  cindy: "Cynthia",
  sandy: "Sandra",
};

// ---------------------------------------------------------------------------
// split_name (dataframe, name, 50)
// ---------------------------------------------------------------------------

function splitName(rows: readonly Row[], column: string): Row[] {
  return rows.map((row) => {
    const val = row[column];
    if (val === null || val === undefined || typeof val !== "string") {
      return { ...row, first_name: null, last_name: null };
    }
    const trimmed = val.trim();
    const lastSpace = trimmed.lastIndexOf(" ");
    if (lastSpace === -1) {
      return { ...row, first_name: trimmed, last_name: "" };
    }
    return {
      ...row,
      first_name: trimmed.slice(0, lastSpace),
      last_name: trimmed.slice(lastSpace + 1),
    };
  });
}

registerTransform(
  { name: "split_name", inputTypes: ["name"], priority: 50, mode: "dataframe" },
  splitName,
);

// ---------------------------------------------------------------------------
// split_name_reverse (dataframe, name, 50)
// ---------------------------------------------------------------------------

function splitNameReverse(rows: readonly Row[], column: string): Row[] {
  return rows.map((row) => {
    const val = row[column];
    if (val === null || val === undefined || typeof val !== "string") {
      return { ...row, first_name: null, last_name: null };
    }
    const commaIdx = val.indexOf(",");
    if (commaIdx === -1) {
      return { ...row, first_name: val.trim(), last_name: "" };
    }
    return {
      ...row,
      last_name: val.slice(0, commaIdx).trim(),
      first_name: val.slice(commaIdx + 1).trim(),
    };
  });
}

registerTransform(
  { name: "split_name_reverse", inputTypes: ["name"], priority: 50, mode: "dataframe" },
  splitNameReverse,
);

// ---------------------------------------------------------------------------
// strip_titles (series, name, 70, auto_apply)
// ---------------------------------------------------------------------------

function stripTitles(values: readonly ColumnValue[]): ColumnValue[] {
  return mapStrings(values, (s) => s.replace(_TITLES, "").trim());
}

registerTransform(
  { name: "strip_titles", inputTypes: ["name"], autoApply: true, priority: 70, mode: "series" },
  stripTitles,
);

// ---------------------------------------------------------------------------
// strip_suffixes (series, name, 60)
// ---------------------------------------------------------------------------

function stripSuffixes(values: readonly ColumnValue[]): ColumnValue[] {
  return mapStrings(values, (s) => s.replace(_SUFFIXES, "").trim());
}

registerTransform(
  { name: "strip_suffixes", inputTypes: ["name"], priority: 60, mode: "series" },
  stripSuffixes,
);

// ---------------------------------------------------------------------------
// name_proper (series, name, 45)
// ---------------------------------------------------------------------------

function nameProper(values: readonly ColumnValue[]): ColumnValue[] {
  return mapStrings(values, (s) => {
    // Title case first
    let result = s.toLowerCase().replace(/\b\w/g, (ch) => ch.toUpperCase());
    // Mc handling — reset _MC_PATTERN.lastIndex since it has the g flag
    result = result.replace(_MC_PATTERN, (_match, letter: string) => `Mc${letter.toUpperCase()}`);
    // O' handling
    result = result.replace(_O_PATTERN, (_match, letter: string) => `O'${letter.toUpperCase()}`);
    return result;
  });
}

registerTransform(
  { name: "name_proper", inputTypes: ["name"], priority: 45, mode: "series" },
  nameProper,
);

// ---------------------------------------------------------------------------
// initial_expand (series, name, 40)
// ---------------------------------------------------------------------------

function initialExpand(values: readonly ColumnValue[]): [ColumnValue[], number[]] {
  const flagged: number[] = [];
  const result: ColumnValue[] = values.map((v, i) => {
    if (v !== null && typeof v === "string" && _INITIAL_PATTERN.test(v)) {
      flagged.push(i);
    }
    return v === undefined ? null : (v as ColumnValue);
  });
  return [result, flagged];
}

registerTransform(
  { name: "initial_expand", inputTypes: ["name"], priority: 40, mode: "series" },
  initialExpand,
);

// ---------------------------------------------------------------------------
// nickname_standardize (series, name, 42)
// ---------------------------------------------------------------------------

function nicknameStandardize(values: readonly ColumnValue[]): ColumnValue[] {
  return mapStrings(values, (s) => {
    const lookup = s.trim().toLowerCase();
    return _NICKNAMES[lookup] ?? s;
  });
}

registerTransform(
  { name: "nickname_standardize", inputTypes: ["name"], priority: 42, mode: "series" },
  nicknameStandardize,
);

// ---------------------------------------------------------------------------
// merge_name (dataframe, name, 45, param: last_name_col="last_name")
// ---------------------------------------------------------------------------

function mergeName(
  rows: readonly Row[],
  column: string,
  lastNameCol: unknown = "last_name",
): Row[] {
  const lnCol = typeof lastNameCol === "string" ? lastNameCol : "last_name";

  // If no rows or first row lacks the last_name column, return unchanged
  if (rows.length > 0 && !(lnCol in rows[0]!)) {
    return rows.map((r) => ({ ...r }));
  }

  return rows.map((row) => {
    const first = row[column];
    const last = row[lnCol];
    const parts: string[] = [];
    if (first !== null && first !== undefined) {
      const s = String(first).trim();
      if (s) parts.push(s);
    }
    if (last !== null && last !== undefined) {
      const s = String(last).trim();
      if (s) parts.push(s);
    }
    return { ...row, full_name: parts.length > 0 ? parts.join(" ") : null };
  });
}

registerTransform(
  { name: "merge_name", inputTypes: ["name"], priority: 45, mode: "dataframe" },
  mergeName,
);
