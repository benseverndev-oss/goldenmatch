import type { ColumnValue, DomainPack } from "../types.js";
import { makeConfig } from "../types.js";
import { registerTransform } from "../transforms/registry.js";

const SSN_RE = /^(\d{3})-?(\d{2})-?(\d{4})$/;

function ssnValidate(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null || typeof v !== "string") return v;
    const m = v.trim().match(SSN_RE);
    if (!m) return false;
    if (m[1] === "000" || m[2] === "00" || m[3] === "0000") return false;
    return true;
  });
}

registerTransform(
  { name: "ssn_validate", inputTypes: ["ssn", "string"], priority: 55, mode: "series" },
  ssnValidate,
);

export const PACK: DomainPack = {
  name: "people_hr",
  description: "Name parsing, SSN formatting, employment dates, gender/boolean standardization",
  transforms: [
    "split_name", "split_name_reverse", "strip_titles", "strip_suffixes",
    "name_proper", "ssn_mask", "ssn_validate",
    "date_iso8601", "gender_standardize", "boolean_normalize",
  ],
  defaultConfig: makeConfig({
    transforms: [
      { column: "name", ops: ["strip", "strip_titles", "title_case"] },
      { column: "ssn", ops: ["ssn_validate"] },
      { column: "gender", ops: ["gender_standardize"] },
      { column: "hire_date", ops: ["date_iso8601"] },
      { column: "active", ops: ["boolean_normalize"] },
    ],
  }),
};
