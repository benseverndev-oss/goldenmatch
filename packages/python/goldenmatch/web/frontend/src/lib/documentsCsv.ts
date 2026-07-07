export function recordsToCsv(
  records: Record<string, unknown>[],
  columns: string[],
): string {
  const cell = (v: unknown): string => {
    if (v === null || v === undefined) return "";
    const s = String(v);
    return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [columns.join(",")];
  for (const r of records) lines.push(columns.map((c) => cell(r[c])).join(","));
  return lines.join("\r\n") + "\r\n";
}
