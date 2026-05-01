import type { ColumnValue, DomainPack } from "../types.js";
import { makeConfig } from "../types.js";
import { registerTransform } from "../transforms/registry.js";

function npiValidate(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null || typeof v !== "string") return v;
    const digits = v.replace(/\D/g, "");
    if (digits.length !== 10) return false;
    const full = "80840" + digits;
    let total = 0;
    for (let i = full.length - 1, pos = 0; i >= 0; i--, pos++) {
      let n = parseInt(full[i]!, 10);
      if (pos % 2 === 1) {
        n *= 2;
        if (n > 9) n -= 9;
      }
      total += n;
    }
    return total % 10 === 0;
  });
}

function icd10Format(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null || typeof v !== "string") return v;
    const code = v.trim().toUpperCase().replace(/\./g, "");
    return code.length > 3 ? code.slice(0, 3) + "." + code.slice(3) : code;
  });
}

registerTransform(
  { name: "npi_validate", inputTypes: ["string"], priority: 50, mode: "series" },
  npiValidate,
);
registerTransform(
  { name: "icd10_format", inputTypes: ["string"], priority: 50, mode: "series" },
  icd10Format,
);

export const PACK: DomainPack = {
  name: "healthcare",
  description: "MRN normalization, ICD-10 formatting, NPI validation, date standardization",
  transforms: ["npi_validate", "icd10_format", "date_iso8601", "null_standardize", "strip"],
  defaultConfig: makeConfig({
    transforms: [
      { column: "npi", ops: ["npi_validate"] },
      { column: "icd10_code", ops: ["icd10_format"] },
      { column: "service_date", ops: ["date_iso8601"] },
      { column: "patient_name", ops: ["strip", "title_case"] },
    ],
  }),
};
