/**
 * config-wizard.ts -- the `init` interactive config wizard.
 *
 * Ports `goldenmatch/config/wizard.py`. The heuristic suggestion tables
 * (`suggestTransforms` / `suggestScorer`) are byte-faithful to the Python ones so
 * the two wizards propose the same config for the same column names -- that's the
 * part worth parity, and it's what the tests pin.
 *
 * The flow takes an injectable `Ask`, so the whole wizard is testable by scripting
 * a session (Python's is untestable because it calls Rich prompts inline).
 */
import type { Ask } from "./interactive.js";
import { askWithDefault, askYesNo, askChoice } from "./interactive.js";

const NAME_KEYWORDS = ["name", "first", "last", "fname", "lname", "given", "surname", "full_name"];
const EMAIL_KEYWORDS = ["email", "mail", "e_mail"];
const PHONE_KEYWORDS = ["phone", "tel", "mobile", "fax", "cell"];
const ZIP_KEYWORDS = ["zip", "postal", "postcode", "zip_code"];
const ADDRESS_KEYWORDS = ["address", "addr", "street", "city", "state", "country"];

function matchesAny(col: string, keywords: readonly string[]): boolean {
  return keywords.some((k) => col.includes(k));
}

/** Python `suggest_transforms` -- same order, same outputs. */
export function suggestTransforms(columnName: string): string[] {
  const col = columnName.toLowerCase();
  if (matchesAny(col, NAME_KEYWORDS)) return ["lowercase", "strip", "normalize_whitespace"];
  if (matchesAny(col, EMAIL_KEYWORDS)) return ["lowercase", "strip"];
  if (matchesAny(col, PHONE_KEYWORDS)) return ["digits_only"];
  if (matchesAny(col, ZIP_KEYWORDS)) return ["strip", "substring:0:5"];
  if (matchesAny(col, ADDRESS_KEYWORDS)) return ["lowercase", "strip", "normalize_whitespace"];
  return ["strip"];
}

/** Python `suggest_scorer` -- same order, same outputs. */
export function suggestScorer(columnName: string): string {
  const col = columnName.toLowerCase();
  if (matchesAny(col, NAME_KEYWORDS)) return "jaro_winkler";
  if (matchesAny(col, EMAIL_KEYWORDS)) return "levenshtein";
  if (matchesAny(col, PHONE_KEYWORDS)) return "exact";
  if (matchesAny(col, ZIP_KEYWORDS)) return "exact";
  if (matchesAny(col, ADDRESS_KEYWORDS)) return "token_sort";
  return "jaro_winkler";
}

const SCORERS = ["exact", "jaro_winkler", "levenshtein", "token_sort", "soundex_match"];

export interface WizardConfig {
  matchkeys: Array<Record<string, unknown>>;
  blocking?: Record<string, unknown>;
  output: Record<string, string>;
}

/** Basename without extension -- the default source label, as Python's `Path.stem`. */
function stem(p: string): string {
  const base = p.split(/[/\\]/).pop() ?? p;
  const dot = base.lastIndexOf(".");
  return dot > 0 ? base.slice(0, dot) : base;
}

/**
 * Run the wizard. Returns the config object; the caller serializes + writes it,
 * so this function does no I/O and can be tested end-to-end on a scripted session.
 */
export async function runWizard(
  ask: Ask,
  out: (s: string) => void = () => {},
): Promise<WizardConfig> {
  out("GoldenMatch Config Wizard\n");

  const mode = await askChoice(ask, "What would you like to do? [dedupe/match]: ", ["dedupe", "match"], "dedupe");

  // File selection is collected for the operator's benefit (Python prompts for it
  // too) but, exactly as in Python, does NOT land in the emitted config -- files
  // are CLI arguments, not config keys.
  if (mode === "dedupe") {
    for (;;) {
      const path = await ask("Enter input file path: ");
      await askWithDefault(ask, "Source label", stem(path.trim()));
      if (!(await askYesNo(ask, "Add another file?", false))) break;
    }
  } else {
    const target = await ask("Enter target file path: ");
    await askWithDefault(ask, "Target source label", stem(target.trim()));
    for (;;) {
      const ref = await ask("Enter reference file path: ");
      await askWithDefault(ask, "Reference source label", stem(ref.trim()));
      if (!(await askYesNo(ask, "Add another reference file?", false))) break;
    }
  }

  // ---- matchkeys ----
  out("\nMatchkey Configuration");
  const matchkeys: Array<Record<string, unknown>> = [];
  for (;;) {
    const mkName = await askWithDefault(ask, "Matchkey name", `mk_${matchkeys.length + 1}`);
    const mkType = await askChoice(ask, "Matchkey type [exact/weighted]: ", ["exact", "weighted"], "exact");

    const fields: Array<Record<string, unknown>> = [];
    for (;;) {
      const fieldName = (await ask("  Field/column name: ")).trim();
      const suggested = suggestTransforms(fieldName);
      out(`  Suggested transforms: ${JSON.stringify(suggested)}`);
      const useSuggested = await askYesNo(ask, "  Use suggested transforms?", true);
      const field: Record<string, unknown> = {
        field: fieldName,
        transforms: useSuggested ? suggested : [],
      };

      if (mkType === "weighted") {
        let scorer = suggestScorer(fieldName);
        out(`  Suggested scorer: ${scorer}`);
        if (!(await askYesNo(ask, "  Use suggested scorer?", true))) {
          scorer = await askChoice(ask, `  Scorer [${SCORERS.join("/")}]: `, SCORERS, scorer);
        }
        const weightRaw = await askWithDefault(ask, "  Weight", "1.0");
        const weight = Number.parseFloat(weightRaw);
        field["scorer"] = scorer;
        field["weight"] = Number.isFinite(weight) ? weight : 1.0;
      }
      fields.push(field);
      if (!(await askYesNo(ask, "  Add another field to this matchkey?", false))) break;
    }

    const mk: Record<string, unknown> = { name: mkName, type: mkType, fields };
    if (mkType === "weighted") {
      const thrRaw = await askWithDefault(ask, "  Match threshold (0.0-1.0)", "0.8");
      const thr = Number.parseFloat(thrRaw);
      mk["threshold"] = Number.isFinite(thr) ? thr : 0.8;
    }
    matchkeys.push(mk);
    if (!(await askYesNo(ask, "Add another matchkey?", false))) break;
  }

  // ---- blocking ----
  // Python's condition: prompt only when there is no weighted matchkey; a weighted
  // matchkey forces blocking configuration without asking.
  const hasWeighted = matchkeys.some((mk) => mk["type"] === "weighted");
  let blocking: Record<string, unknown> | undefined;
  if (hasWeighted || (await askYesNo(ask, "\nConfigure blocking?", hasWeighted))) {
    out("\nBlocking Configuration");
    const keys: Array<Record<string, unknown>> = [];
    for (;;) {
      const raw = await ask("  Blocking fields (comma-separated): ");
      const blockFields = raw.split(",").map((s) => s.trim()).filter((s) => s !== "");
      if (blockFields.length > 0) {
        keys.push({
          fields: blockFields,
          transforms: suggestTransforms(blockFields[0]!),
        });
      }
      if (!(await askYesNo(ask, "  Add another blocking key?", false))) break;
    }
    if (keys.length > 0) blocking = { strategy: "static", keys };
  }

  // ---- output ----
  out("\nOutput Configuration");
  const format = await askChoice(ask, "Output format [csv/parquet]: ", ["csv", "parquet"], "csv");
  const directory = await askWithDefault(ask, "Output directory", "./output");
  const runName = (await askWithDefault(ask, "Run name (optional)", "")).trim();

  const output: Record<string, string> = { format, directory };
  if (runName !== "") output["run_name"] = runName;

  return { matchkeys, ...(blocking ? { blocking } : {}), output };
}

/**
 * Serialize the wizard config to YAML.
 *
 * Deliberately hand-rolled rather than pulling the optional `yaml` peer dep: the
 * shape here is a closed set (strings, finite numbers, string arrays, nested
 * objects/arrays), so a targeted emitter is safer than making `init` fail on a
 * missing optional dependency. Key ORDER is preserved to match Python's
 * `sort_keys=False`.
 */
export function toYaml(value: unknown, indent = 0): string {
  const pad = "  ".repeat(indent);
  if (Array.isArray(value)) {
    if (value.length === 0) return `${pad}[]\n`;
    return value
      .map((item) => {
        if (item !== null && typeof item === "object" && !Array.isArray(item)) {
          const body = toYaml(item, indent + 1);
          // Hoist the first key onto the dash line.
          const lines = body.split("\n").filter((l) => l !== "");
          const first = lines[0]!.slice((indent + 1) * 2);
          const rest = lines.slice(1).join("\n");
          return `${pad}- ${first}${rest ? "\n" + rest : ""}`;
        }
        return `${pad}- ${scalar(item)}`;
      })
      .join("\n") + "\n";
  }
  if (value !== null && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.length === 0) return `${pad}{}\n`;
    return entries
      .map(([k, v]) => {
        if (v !== null && typeof v === "object") {
          const body = toYaml(v, indent + 1);
          return `${pad}${k}:\n${body.replace(/\n$/, "")}`;
        }
        return `${pad}${k}: ${scalar(v)}`;
      })
      .join("\n") + "\n";
  }
  return `${pad}${scalar(value)}\n`;
}

function scalar(v: unknown): string {
  if (v === null || v === undefined) return "null";
  if (typeof v === "boolean" || typeof v === "number") return String(v);
  const s = String(v);
  // Quote when YAML would otherwise reinterpret the value.
  if (s === "" || /^[\s]|[\s]$|[:#\-{}[\],&*?|>%@`"']/.test(s) || /^(true|false|null|yes|no|on|off|~)$/i.test(s) || /^-?\d/.test(s)) {
    return `"${s.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
  }
  return s;
}
