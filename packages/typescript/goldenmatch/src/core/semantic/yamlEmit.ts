/**
 * yamlEmit.ts — a minimal block-style YAML serializer matching Python
 * `yaml.safe_dump(obj, sort_keys=False, default_flow_style=False)` for the value
 * space the semantic-layer catalog emitters produce (nested string/number/boolean/
 * null maps + lists of maps).
 *
 * This is NOT a general YAML library: it reproduces PyYAML's block emission +
 * plain-scalar quoting rules for the shapes here (identifiers, `ref('x')`,
 * `{CUBE}...` SQL, version strings, ints, floats, bools), locked byte-for-byte
 * against Python fixtures. It exists so the TS catalog emitters conform to the
 * Python ones without pulling in a YAML dependency.
 */

/**
 * A number that must render as a Python float — i.e. keep a decimal point even
 * when integer-valued (`repr(0.0)` == "0.0", but JS `String(0)` == "0"). Wrap a
 * value with `pyFloat` so the serializer emits it as a bare float scalar.
 */
export class PyFloat {
  constructor(public readonly value: number) {}
}

/** Ordered map preserving insertion order (PyYAML `sort_keys=False`). */
export type YamlValue =
  | string
  | number
  | boolean
  | null
  | PyFloat
  | YamlValue[]
  | { [k: string]: YamlValue };

// --- PyYAML implicit-type resolvers (YAML 1.1 core, as PyYAML uses) ----------
// A plain string that parses back as one of these must be quoted so it stays a
// string. Patterns mirror PyYAML's SafeRepresenter resolvers.
const BOOL_RE = /^(?:yes|Yes|YES|no|No|NO|true|True|TRUE|false|False|FALSE|on|On|ON|off|Off|OFF)$/;
const NULL_RE = /^(?:~|null|Null|NULL|)$/;
const INT_RE =
  /^(?:[-+]?0b[0-1_]+|[-+]?0[0-7_]+|[-+]?(?:0|[1-9][0-9_]*)|[-+]?0x[0-9a-fA-F_]+|[-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+)$/;
const FLOAT_RE =
  /^(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+]?[0-9]+)?|\.[0-9][0-9_]*(?:[eE][-+][0-9]+)?|[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$/;

function parsesAsNonString(s: string): boolean {
  return BOOL_RE.test(s) || NULL_RE.test(s) || INT_RE.test(s) || FLOAT_RE.test(s);
}

// Chars that, as the FIRST char of a plain scalar, force quoting.
const LEADING_INDICATORS = new Set([",", "[", "]", "{", "}", "#", "&", "*", "!", "|", ">", "'", '"', "%", "@", "`"]);

/** Does this string need single-quoting under PyYAML's plain-scalar rules? */
function needsSingleQuote(s: string): boolean {
  if (s.length === 0) return true;
  if (parsesAsNonString(s)) return true;
  // leading / trailing whitespace
  if (/^\s/.test(s) || /\s$/.test(s)) return true;
  const first = s[0]!;
  if (LEADING_INDICATORS.has(first)) return true;
  // '-', '?', ':' lead only when followed by a space (or at end)
  if ((first === "-" || first === "?" || first === ":") && (s.length === 1 || s[1] === " ")) return true;
  // ": " or trailing ":" anywhere; " #" anywhere
  if (s.includes(": ") || s.endsWith(":") || s.includes(" #")) return true;
  return false;
}

function scalar(v: string | number | boolean | null | PyFloat): string {
  if (v === null) return "null";
  if (v instanceof PyFloat) return floatScalar(v.value);
  if (typeof v === "boolean") return v ? "true" : "false";
  // A bare JS number renders as a Python int (no decimal). Semantically-float
  // values reach here wrapped in `PyFloat` (see `pyFloat`).
  if (typeof v === "number") return String(v);
  // string
  if (needsSingleQuote(v)) return `'${v.replace(/'/g, "''")}'`;
  return v;
}

/** Match Python's `repr` for a float: keep the decimal point even when the
 * value is integer (`repr(0.0)` == "0.0", but JS `String(0)` == "0"); non-integer
 * floats round-trip identically in both languages for these decimals. */
function floatScalar(n: number): string {
  if (Number.isInteger(n)) return `${n}.0`;
  return String(n);
}

/**
 * Wrap a number that must serialize as a Python float (a bare number serializes
 * as a Python int). The emitters use this for `reduction_ratio` so `0.0`/`0.2`
 * emit as bare floats matching `yaml.safe_dump`.
 */
export function pyFloat(n: number): PyFloat {
  return new PyFloat(n);
}

function isPlainObject(v: YamlValue): v is { [k: string]: YamlValue } {
  return typeof v === "object" && v !== null && !Array.isArray(v) && !(v instanceof PyFloat);
}

function emitNode(v: YamlValue, indent: number, lines: string[]): void {
  const pad = "  ".repeat(indent);
  if (Array.isArray(v)) {
    for (const item of v) {
      if (isPlainObject(item)) {
        // "- " then the map's first key on the same line, rest indented.
        emitMapInline(item, indent, `${pad}- `, lines);
      } else if (Array.isArray(item)) {
        // Nested block sequence: PyYAML puts the inner sequence's first marker
        // inline after the outer "- " (e.g. `- - a`), rest at the next indent.
        emitSeqInline(item, indent, `${pad}- `, lines);
      } else {
        lines.push(`${pad}- ${scalar(item)}`);
      }
    }
    return;
  }
  if (isPlainObject(v)) {
    for (const [k, val] of Object.entries(v)) {
      emitKey(k, val, indent, pad, lines);
    }
    return;
  }
  lines.push(`${pad}${scalar(v)}`);
}

function emitKey(k: string, val: YamlValue, indent: number, pad: string, lines: string[]): void {
  const key = scalar(k);
  if (isPlainObject(val)) {
    if (Object.keys(val).length === 0) {
      lines.push(`${pad}${key}: {}`);
    } else {
      lines.push(`${pad}${key}:`);
      emitNode(val, indent + 1, lines);
    }
  } else if (Array.isArray(val)) {
    if (val.length === 0) {
      lines.push(`${pad}${key}: []`);
    } else {
      // PyYAML block sequences sit at the SAME indent as their key.
      lines.push(`${pad}${key}:`);
      emitNode(val, indent, lines);
    }
  } else {
    lines.push(`${pad}${key}: ${scalar(val)}`);
  }
}

/** Emit a nested block sequence whose first item's `- ` marker shares the line
 * with `prefix` (the outer sequence item's "- "), e.g. `- - a` / `- - a\n  - b`.
 * Mirrors PyYAML's inline nested-sequence layout. */
function emitSeqInline(arr: YamlValue[], indent: number, prefix: string, lines: string[]): void {
  if (arr.length === 0) {
    lines.push(`${prefix}[]`);
    return;
  }
  const childPad = "  ".repeat(indent + 1);
  arr.forEach((el, i) => {
    const p = i === 0 ? prefix : childPad;
    if (isPlainObject(el)) {
      emitMapInline(el, indent + 1, `${p}- `, lines);
    } else if (Array.isArray(el)) {
      emitSeqInline(el, indent + 1, `${p}- `, lines);
    } else {
      lines.push(`${p}- ${scalar(el)}`);
    }
  });
}

/** Emit a map whose first key shares a line with `prefix` (the "- " of a
 * sequence item), remaining keys indented to align under it. */
function emitMapInline(
  obj: { [k: string]: YamlValue },
  indent: number,
  prefix: string,
  lines: string[],
): void {
  const entries = Object.entries(obj);
  const childIndent = indent + 1;
  const childPad = "  ".repeat(childIndent);
  entries.forEach(([k, val], i) => {
    const linePad = i === 0 ? prefix : childPad;
    const key = scalar(k);
    if (isPlainObject(val)) {
      if (Object.keys(val).length === 0) lines.push(`${linePad}${key}: {}`);
      else {
        lines.push(`${linePad}${key}:`);
        emitNode(val, childIndent + 1, lines);
      }
    } else if (Array.isArray(val)) {
      if (val.length === 0) lines.push(`${linePad}${key}: []`);
      else {
        lines.push(`${linePad}${key}:`);
        emitNode(val, childIndent, lines);
      }
    } else {
      lines.push(`${linePad}${key}: ${scalar(val)}`);
    }
  });
}

/**
 * Serialize a value to block-style YAML matching Python
 * `yaml.safe_dump(value, sort_keys=False, default_flow_style=False)` — including
 * the trailing newline. Floats that must keep a decimal point should be wrapped
 * with `pyFloat` by the caller before insertion (the emitters do this for
 * `reduction_ratio`).
 */
export function dumpYaml(value: YamlValue): string {
  const lines: string[] = [];
  emitNode(value, 0, lines);
  return lines.join("\n") + "\n";
}
