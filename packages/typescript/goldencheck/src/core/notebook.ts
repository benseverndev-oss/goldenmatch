/**
 * Notebook / HTML rendering — Jupyter/Colab display hooks.
 * Port of goldencheck/notebook.py.
 * Edge-safe: no Node.js dependencies.
 */

import type { Finding, DatasetProfile, ColumnProfile } from "./types.js";
import { Severity, severityLabel, healthScore } from "./types.js";

// --- Constants ---

const SEVERITY_COLORS: Record<number, string> = {
  [Severity.ERROR]: "#ff4444",
  [Severity.WARNING]: "#ffbb33",
  [Severity.INFO]: "#33b5e5",
};

const GRADE_COLORS: Record<string, string> = {
  A: "#00ff00",
  B: "#7fff00",
  C: "#ffff00",
  D: "#ff7f00",
  F: "#ff0000",
};

// --- HTML helpers ---

/** Escape HTML special characters. */
function esc(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** Format confidence as H/M/L. */
function confLabel(confidence: number): string {
  if (confidence >= 0.8) return "H";
  if (confidence >= 0.5) return "M";
  return "L";
}

// --- Finding HTML ---

function findingToHtml(f: Finding): string {
  const color = SEVERITY_COLORS[f.severity] ?? "#888";
  const label = severityLabel(f.severity);
  const conf = confLabel(f.confidence);
  const source = f.source === "llm" ? " [LLM]" : "";
  const samples = f.sampleValues.slice(0, 3).map(esc).join(", ");
  const borderColor = color;

  return (
    `<tr style="border-left:3px solid ${borderColor}">` +
    `<td style="color:${color};font-weight:bold;padding:4px 8px">${label}</td>` +
    `<td style="padding:4px 8px">${esc(f.column)}</td>` +
    `<td style="padding:4px 8px">${esc(f.check)}</td>` +
    `<td style="padding:4px 8px">${esc(f.message)}</td>` +
    `<td style="text-align:right;padding:4px 8px">${f.affectedRows}</td>` +
    `<td style="padding:4px 8px">${conf}${source}</td>` +
    `<td style="color:#888;font-size:0.85em;padding:4px 8px">${samples}</td>` +
    `</tr>`
  );
}

/**
 * Render an array of findings as an HTML table.
 * Columns: Severity, Column, Check, Message, Rows, Conf, Samples.
 */
export function findingsToHtml(findings: readonly Finding[]): string {
  const header =
    '<table style="border-collapse:collapse;width:100%;font-family:monospace;font-size:13px">' +
    '<thead><tr style="border-bottom:2px solid #444;text-align:left">' +
    "<th>Severity</th><th>Column</th><th>Check</th>" +
    "<th>Message</th><th>Rows</th><th>Conf</th><th>Samples</th>" +
    "</tr></thead><tbody>";

  const rows = findings.map(findingToHtml).join("");
  return header + rows + "</tbody></table>";
}

// --- Profile HTML ---

/** Render health grade badge as inline HTML span. */
function healthBadge(grade: string, score: number): string {
  const color = GRADE_COLORS[grade] ?? "#888";
  return (
    `<span style="background:${color};color:#000;padding:2px 8px;` +
    `border-radius:4px;font-weight:bold;font-size:1.1em">` +
    `${grade} (${score})</span>`
  );
}

/** Render a single column profile row. */
function colProfileRow(col: ColumnProfile): string {
  const top = col.topValues
    .slice(0, 3)
    .map(([v, c]) => `${esc(String(v))}(${c})`)
    .join(", ");

  return (
    `<tr>` +
    `<td style="font-weight:bold;padding:4px 8px">${esc(col.name)}</td>` +
    `<td style="padding:4px 8px">${esc(col.inferredType)}</td>` +
    `<td style="text-align:right;padding:4px 8px">${col.nullPct.toFixed(1)}%</td>` +
    `<td style="text-align:right;padding:4px 8px">${col.uniquePct.toFixed(1)}%</td>` +
    `<td style="color:#888;font-size:0.85em;padding:4px 8px">${top}</td>` +
    `</tr>`
  );
}

/**
 * Render dataset profile as an HTML table with health badge.
 * Columns: Column, Type, Null%, Unique%, Top Values.
 */
export function profileToHtml(
  profile: DatasetProfile,
  findings?: readonly Finding[],
): string {
  let grade: string;
  let score: number;

  if (findings && findings.length > 0) {
    const byCol: Record<string, { errors: number; warnings: number }> = {};
    for (const f of findings) {
      if (f.severity >= Severity.WARNING) {
        if (!byCol[f.column]) byCol[f.column] = { errors: 0, warnings: 0 };
        if (f.severity === Severity.ERROR) byCol[f.column]!.errors++;
        else byCol[f.column]!.warnings++;
      }
    }
    const hs = healthScore(byCol);
    grade = hs.grade;
    score = hs.points;
  } else {
    const hs = healthScore();
    grade = hs.grade;
    score = hs.points;
  }

  const badge = healthBadge(grade, score);
  const rowCountFmt = profile.rowCount.toLocaleString("en-US");

  const header =
    `<div style="font-family:monospace;font-size:13px">` +
    `<div style="margin-bottom:8px">` +
    `<strong>${esc(profile.filePath)}</strong> &mdash; ` +
    `${rowCountFmt} rows, ${profile.columnCount} columns ` +
    `&mdash; Health: ${badge}</div>` +
    `<table style="border-collapse:collapse;width:100%">` +
    `<thead><tr style="border-bottom:2px solid #444;text-align:left">` +
    `<th>Column</th><th>Type</th><th>Null%</th><th>Unique%</th><th>Top Values</th>` +
    `</tr></thead><tbody>`;

  const rows = profile.columns.map(colProfileRow).join("");
  return header + rows + "</tbody></table></div>";
}

// --- ScanResult class ---

/**
 * Wrapper for scan results with rich HTML display.
 * Mirrors Python's ScanResult with _repr_html_ for Jupyter/Colab.
 */
export class ScanResult {
  public readonly findings: readonly Finding[];
  public readonly profile: DatasetProfile;

  constructor(findings: readonly Finding[], profile: DatasetProfile) {
    this.findings = findings;
    this.profile = profile;
  }

  /** Full HTML rendering — findings table + profile table. */
  toHtml(): string {
    const parts = [
      '<div style="font-family:monospace">',
      '<h3 style="color:#FFD700;margin:0 0 12px 0">GoldenCheck Results</h3>',
      profileToHtml(this.profile, this.findings),
      '<div style="margin-top:16px">',
      `<strong>${this.findings.length} findings</strong>`,
      "</div>",
      findingsToHtml(this.findings),
      "</div>",
    ];
    return parts.join("\n");
  }

  /** Text summary — e.g. "ScanResult(5 findings: 2 errors, 3 warnings)". */
  toString(): string {
    const errors = this.findings.filter((f) => f.severity === Severity.ERROR).length;
    const warnings = this.findings.filter((f) => f.severity === Severity.WARNING).length;
    return (
      `ScanResult(${this.findings.length} findings: ` +
      `${errors} errors, ${warnings} warnings)`
    );
  }
}
