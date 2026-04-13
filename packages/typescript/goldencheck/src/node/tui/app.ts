/**
 * Terminal TUI — ANSI-based output for scan results.
 * Port of goldencheck/tui/app.py (simplified: no interactive tabs, just rich output).
 * Zero additional dependencies — uses raw ANSI escape codes.
 */

import type { Finding, DatasetProfile } from "../../core/types.js";
import { Severity, severityLabel, healthScore } from "../../core/types.js";

// --- ANSI escape codes ---

const RESET = "\x1b[0m";
const BOLD = "\x1b[1m";
const DIM = "\x1b[2m";

const RED = "\x1b[31m";
const GREEN = "\x1b[32m";
const YELLOW = "\x1b[33m";
const BLUE = "\x1b[34m";
const MAGENTA = "\x1b[35m";
const CYAN = "\x1b[36m";
const WHITE = "\x1b[37m";

// Orange approximation (256-color)
const ORANGE = "\x1b[38;5;208m";
// Purple (256-color)
const PURPLE = "\x1b[38;5;141m";

// --- Helpers ---

/** Map severity to ANSI color. */
function severityColor(s: Severity): string {
  switch (s) {
    case Severity.ERROR:
      return RED;
    case Severity.WARNING:
      return YELLOW;
    case Severity.INFO:
      return CYAN;
  }
}

/** Map health grade to ANSI color. */
function gradeColor(grade: string): string {
  switch (grade) {
    case "A":
      return GREEN;
    case "B":
      return "\x1b[38;5;118m"; // chartreuse
    case "C":
      return YELLOW;
    case "D":
      return ORANGE;
    case "F":
      return RED;
    default:
      return WHITE;
  }
}

/** Format confidence as H/M/L. */
function confLabel(confidence: number): string {
  if (confidence >= 0.8) return "H";
  if (confidence >= 0.5) return "M";
  return "L";
}

/** Source badge string. */
function sourceBadge(source: string | null): string {
  if (source === "llm") return ` ${PURPLE}[LLM]${RESET}`;
  if (source === "baseline_drift") return ` ${ORANGE}[DRIFT]${RESET}`;
  return "";
}

/** Pad or truncate string to width (visible characters only). */
function pad(text: string, width: number): string {
  if (text.length > width) {
    return text.slice(0, width - 1) + "\u2026";
  }
  return text.padEnd(width);
}

/** Right-align a string to width. */
function padRight(text: string, width: number): string {
  if (text.length > width) {
    return text.slice(0, width);
  }
  return text.padStart(width);
}

/** Format a number with comma separators. */
function formatNumber(n: number): string {
  return n.toLocaleString("en-US");
}

/** Draw a horizontal rule. */
function hr(width: number, char = "\u2500"): string {
  return char.repeat(width);
}

// --- Column widths ---

const COL_WIDTHS = {
  severity: 9,
  column: 18,
  check: 22,
  message: 38,
  rows: 8,
  conf: 6,
} as const;

const TABLE_WIDTH =
  COL_WIDTHS.severity +
  COL_WIDTHS.column +
  COL_WIDTHS.check +
  COL_WIDTHS.message +
  COL_WIDTHS.rows +
  COL_WIDTHS.conf +
  5; // 5 separators

// --- Main render function ---

/**
 * Render scan findings and profile to stdout using ANSI-colored output.
 * Mirrors the 4 tabs of the Python Textual TUI in a linear format.
 *
 * Sections:
 *   1. Header — file path, rows, columns, health grade badge
 *   2. Summary — error/warning/info counts
 *   3. Findings table — severity, column, check, message, rows, confidence
 *   4. Column detail — per-column stats
 */
export function renderTui(findings: readonly Finding[], profile: DatasetProfile): void {
  const lines: string[] = [];

  // -- Compute health score --
  const byCol: Record<string, { errors: number; warnings: number }> = {};
  for (const f of findings) {
    if (f.severity >= Severity.WARNING) {
      if (!byCol[f.column]) byCol[f.column] = { errors: 0, warnings: 0 };
      if (f.severity === Severity.ERROR) byCol[f.column]!.errors++;
      else byCol[f.column]!.warnings++;
    }
  }
  const score = healthScore(Object.keys(byCol).length > 0 ? byCol : undefined);

  const errors = findings.filter((f) => f.severity === Severity.ERROR).length;
  const warnings = findings.filter((f) => f.severity === Severity.WARNING).length;
  const infos = findings.length - errors - warnings;

  // =====================
  // Section 1: Header
  // =====================
  lines.push("");
  lines.push(
    `${BOLD}${YELLOW}  GoldenCheck${RESET}  ${DIM}${profile.filePath}${RESET}`,
  );
  lines.push(`  ${DIM}${hr(TABLE_WIDTH)}${RESET}`);
  lines.push(
    `  ${BOLD}Rows:${RESET} ${formatNumber(profile.rowCount)}` +
      `  ${BOLD}Columns:${RESET} ${profile.columnCount}` +
      `  ${BOLD}Health:${RESET} ${gradeColor(score.grade)}${BOLD}${score.grade}${RESET}` +
      ` ${DIM}(${score.points}/100)${RESET}`,
  );
  lines.push("");

  // =====================
  // Section 2: Summary
  // =====================
  lines.push(
    `  ${RED}${BOLD}${errors}${RESET} ${RED}errors${RESET}` +
      `  ${YELLOW}${BOLD}${warnings}${RESET} ${YELLOW}warnings${RESET}` +
      `  ${CYAN}${BOLD}${infos}${RESET} ${CYAN}info${RESET}` +
      `  ${DIM}(${findings.length} total)${RESET}`,
  );
  lines.push("");

  // =====================
  // Section 3: Findings
  // =====================
  if (findings.length > 0) {
    lines.push(`  ${BOLD}Findings${RESET}`);
    lines.push(`  ${DIM}${hr(TABLE_WIDTH)}${RESET}`);

    // Table header
    lines.push(
      `  ${BOLD}${pad("Severity", COL_WIDTHS.severity)}${RESET}` +
        ` ${BOLD}${pad("Column", COL_WIDTHS.column)}${RESET}` +
        ` ${BOLD}${pad("Check", COL_WIDTHS.check)}${RESET}` +
        ` ${BOLD}${pad("Message", COL_WIDTHS.message)}${RESET}` +
        ` ${BOLD}${padRight("Rows", COL_WIDTHS.rows)}${RESET}` +
        ` ${BOLD}${padRight("Conf", COL_WIDTHS.conf)}${RESET}`,
    );
    lines.push(`  ${DIM}${hr(TABLE_WIDTH)}${RESET}`);

    // Sort: ERROR first, then WARNING, then INFO
    const sorted = [...findings].sort((a, b) => b.severity - a.severity);

    for (const f of sorted) {
      const color = severityColor(f.severity);
      const label = severityLabel(f.severity);
      const conf = confLabel(f.confidence);
      const badge = sourceBadge(f.source);

      lines.push(
        `  ${color}${BOLD}${pad(label, COL_WIDTHS.severity)}${RESET}` +
          ` ${pad(f.column, COL_WIDTHS.column)}` +
          ` ${pad(f.check, COL_WIDTHS.check)}` +
          ` ${pad(f.message, COL_WIDTHS.message)}` +
          ` ${padRight(formatNumber(f.affectedRows), COL_WIDTHS.rows)}` +
          ` ${padRight(conf, COL_WIDTHS.conf)}${badge}`,
      );
    }

    lines.push(`  ${DIM}${hr(TABLE_WIDTH)}${RESET}`);
    lines.push("");
  } else {
    lines.push(`  ${GREEN}${BOLD}No findings — data looks clean!${RESET}`);
    lines.push("");
  }

  // =====================
  // Section 4: Column Detail
  // =====================
  if (profile.columns.length > 0) {
    lines.push(`  ${BOLD}Column Detail${RESET}`);
    lines.push(`  ${DIM}${hr(TABLE_WIDTH)}${RESET}`);

    const hdrCol = 18;
    const hdrType = 14;
    const hdrNull = 8;
    const hdrUniq = 8;
    const hdrTop = 40;

    lines.push(
      `  ${BOLD}${pad("Column", hdrCol)}${RESET}` +
        ` ${BOLD}${pad("Type", hdrType)}${RESET}` +
        ` ${BOLD}${padRight("Null%", hdrNull)}${RESET}` +
        ` ${BOLD}${padRight("Uniq%", hdrUniq)}${RESET}` +
        ` ${BOLD}${pad("Top Values", hdrTop)}${RESET}`,
    );
    lines.push(`  ${DIM}${hr(TABLE_WIDTH)}${RESET}`);

    for (const col of profile.columns) {
      const top = col.topValues
        .slice(0, 3)
        .map(([v, c]) => `${v}(${c})`)
        .join(", ");

      lines.push(
        `  ${BOLD}${pad(col.name, hdrCol)}${RESET}` +
          ` ${pad(col.inferredType, hdrType)}` +
          ` ${padRight(col.nullPct.toFixed(1) + "%", hdrNull)}` +
          ` ${padRight(col.uniquePct.toFixed(1) + "%", hdrUniq)}` +
          ` ${DIM}${pad(top, hdrTop)}${RESET}`,
      );
    }

    lines.push(`  ${DIM}${hr(TABLE_WIDTH)}${RESET}`);
    lines.push("");
  }

  // Flush to stdout
  process.stdout.write(lines.join("\n") + "\n");
}
