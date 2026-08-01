/**
 * Tiny read-side helpers for the semantic-model parsers (consume side, wedge A).
 *
 * The Python parsers take a path / YAML string / loaded dict via `_load`. The
 * edge-safe TS core operates on an ALREADY-LOADED document object (a plain JS
 * object, the shape a YAML/JSON parse produces) — the file read + YAML parse is a
 * node concern (see the MCP handler). These helpers mirror Python's defensive
 * `.get(...)` + `isinstance` reads over that untyped document.
 */

/** A loaded semantic-model document (post YAML/JSON parse). */
export type LoadedDoc = Record<string, unknown>;

export function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** A list value, or `[]` for anything else (mirrors `data.get(k) or []`). */
export function asList(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

/** `str(v).strip()`-equivalent for a possibly-missing scalar; "" when absent. */
export function asStrStripped(v: unknown): string {
  if (v === undefined || v === null) return "";
  return String(v).trim();
}

/** Non-stripping string read for cube/osi fields: `""` for nullish (a missing
 * key), otherwise `String(v)`. This is Python's `str(d.get(k, ""))` for the
 * present-value case; it deliberately maps an explicit `null`/`undefined` to `""`
 * rather than Python's `str(None) == "None"`, since a missing field should read as
 * empty, not the literal string "None" (real models never carry a null name/sql). */
export function asStr(v: unknown): string {
  if (v === undefined || v === null) return "";
  return String(v);
}

/** A trimmed non-empty string if `v` is a string with content, else undefined. */
export function optStr(v: unknown): string | undefined {
  if (typeof v !== "string") return undefined;
  const s = v.trim();
  return s.length ? s : undefined;
}
