/**
 * Notebook HTML rendering — generates HTML tables for TransformResult, Manifest, DatasetProfile.
 */

import type { DatasetProfile, Manifest, TransformResult } from "./types.js";

export function transformResultToHtml(result: TransformResult): string {
  const rows = result.rows.length;
  const cols = result.columns.length;
  const transforms = result.manifest.records.length;
  const errors = result.manifest.errors.length;

  let html = `<div style="font-family: monospace; padding: 10px; border: 1px solid #ddd; border-radius: 5px;">
  <h3 style="margin: 0 0 10px 0;">GoldenFlow TransformResult</h3>
  <table style="border-collapse: collapse; width: 100%;">
  <tr><td style="padding: 4px 8px; font-weight: bold;">Rows</td><td>${rows.toLocaleString()}</td></tr>
  <tr><td style="padding: 4px 8px; font-weight: bold;">Columns</td><td>${cols}</td></tr>
  <tr><td style="padding: 4px 8px; font-weight: bold;">Transforms Applied</td><td>${transforms}</td></tr>
  <tr><td style="padding: 4px 8px; font-weight: bold;">Errors</td><td style="color: ${errors ? "red" : "green"};">${errors}</td></tr>
  </table>`;

  if (result.manifest.records.length > 0) {
    html += `<h4 style="margin: 10px 0 5px 0;">Transforms</h4>
    <table style="border-collapse: collapse; width: 100%; font-size: 0.9em;">
    <tr style="background: #f5f5f5;">
      <th style="padding: 4px 8px; text-align: left;">Column</th>
      <th style="padding: 4px 8px; text-align: left;">Transform</th>
      <th style="padding: 4px 8px; text-align: left;">Affected</th>
    </tr>`;
    const shown = result.manifest.records.slice(0, 10);
    for (const r of shown) {
      html += `<tr>
        <td style="padding: 4px 8px;">${r.column}</td>
        <td style="padding: 4px 8px;">${r.transform}</td>
        <td style="padding: 4px 8px;">${r.affectedRows}/${r.totalRows}</td>
      </tr>`;
    }
    if (result.manifest.records.length > 10) {
      html += `<tr><td colspan="3" style="padding: 4px 8px; color: #888;">... and ${result.manifest.records.length - 10} more</td></tr>`;
    }
    html += "</table>";
  }

  html += "</div>";
  return html;
}

export function manifestToHtml(manifest: Manifest): string {
  let html = `<div style="font-family: monospace; padding: 10px; border: 1px solid #ddd; border-radius: 5px;">
  <h3>GoldenFlow Manifest</h3>
  <p>Source: ${manifest.source} | Transforms: ${manifest.records.length} | Errors: ${manifest.errors.length}</p>
  <table style="border-collapse: collapse; width: 100%; font-size: 0.9em;">
  <tr style="background: #f5f5f5;">
    <th style="padding: 4px 8px; text-align: left;">Column</th>
    <th style="padding: 4px 8px; text-align: left;">Transform</th>
    <th style="padding: 4px 8px; text-align: left;">Affected</th>
    <th style="padding: 4px 8px; text-align: left;">Before</th>
    <th style="padding: 4px 8px; text-align: left;">After</th>
  </tr>`;
  for (const r of manifest.records) {
    const before = r.sampleBefore.slice(0, 2).join(", ");
    const after = r.sampleAfter.slice(0, 2).join(", ");
    html += `<tr>
      <td style="padding: 4px 8px;">${r.column}</td>
      <td style="padding: 4px 8px;">${r.transform}</td>
      <td style="padding: 4px 8px;">${r.affectedRows}/${r.totalRows}</td>
      <td style="padding: 4px 8px; color: #c00;">${before}</td>
      <td style="padding: 4px 8px; color: #0a0;">${after}</td>
    </tr>`;
  }
  html += "</table></div>";
  return html;
}

export function profileToHtml(profile: DatasetProfile): string {
  let html = `<div style="font-family: monospace; padding: 10px; border: 1px solid #ddd; border-radius: 5px;">
  <h3>GoldenFlow Profile</h3>
  <p>${profile.rowCount.toLocaleString()} rows, ${profile.columnCount} columns</p>
  <table style="border-collapse: collapse; width: 100%; font-size: 0.9em;">
  <tr style="background: #f5f5f5;">
    <th style="padding: 4px 8px; text-align: left;">Column</th>
    <th style="padding: 4px 8px; text-align: left;">Type</th>
    <th style="padding: 4px 8px; text-align: left;">Nulls</th>
    <th style="padding: 4px 8px; text-align: left;">Unique</th>
    <th style="padding: 4px 8px; text-align: left;">Sample</th>
  </tr>`;
  for (const c of profile.columns) {
    const pct = (c.nullPct * 100).toFixed(0);
    html += `<tr>
      <td style="padding: 4px 8px;">${c.name}</td>
      <td style="padding: 4px 8px;">${c.inferredType}</td>
      <td style="padding: 4px 8px;">${c.nullCount} (${pct}%)</td>
      <td style="padding: 4px 8px;">${c.uniqueCount}</td>
      <td style="padding: 4px 8px; color: #888;">${c.sampleValues.slice(0, 3).join(", ")}</td>
    </tr>`;
  }
  html += "</table></div>";
  return html;
}
