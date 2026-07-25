/**
 * interactive.ts -- shared prompt plumbing for the interactive CLI commands
 * (`label`, `review`, `init`).
 *
 * Node-only (uses `node:readline`), so it lives under `src/node/`.
 *
 * THE POINT OF THIS MODULE: every loop below takes an injectable `Ask` instead of
 * reading stdin directly, so the decision logic is unit-testable without a TTY.
 * Python's equivalents call `console.input(...)` inline, which is why they have no
 * loop tests -- that is the wart this port deliberately does not reproduce.
 */
import { createInterface } from "node:readline";

/** Prompts for one line of input. Injectable so tests can script a session. */
export type Ask = (prompt: string) => Promise<string>;

/** Real stdin-backed prompt. One readline interface for the whole session. */
export function createStdinAsk(): { ask: Ask; close: () => void } {
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  return {
    ask: (prompt: string) => new Promise<string>((resolve) => rl.question(prompt, resolve)),
    close: () => rl.close(),
  };
}

/** Scripted prompt for tests: replays `answers`, then repeats the last one. */
export function scriptedAsk(answers: readonly string[]): Ask {
  let i = 0;
  return async () => answers[Math.min(i++, answers.length - 1)] ?? "q";
}

/**
 * Ask until the answer is one of `allowed` (case-insensitive, trimmed).
 *
 * Mirrors Python's `while True: ... if response in (...)` loop. Returns the
 * fallback if the stream ends (EOF -> empty answers forever), so a piped or
 * closed stdin terminates instead of spinning.
 */
export async function askChoice(
  ask: Ask,
  prompt: string,
  allowed: readonly string[],
  fallback: string,
  onInvalid?: (raw: string) => void,
): Promise<string> {
  for (let attempts = 0; attempts < 100; attempts++) {
    const raw = (await ask(prompt)).trim().toLowerCase();
    if (allowed.includes(raw)) return raw;
    if (raw === "") return fallback;
    onInvalid?.(raw);
  }
  return fallback;
}

/** Ask with a default shown in the prompt; empty input takes the default. */
export async function askWithDefault(
  ask: Ask,
  question: string,
  fallback: string,
): Promise<string> {
  const answer = (await ask(`${question} [${fallback}]: `)).trim();
  return answer === "" ? fallback : answer;
}

/** Yes/no with a default. */
export async function askYesNo(
  ask: Ask,
  question: string,
  fallback: boolean,
): Promise<boolean> {
  const hint = fallback ? "Y/n" : "y/N";
  const answer = (await ask(`${question} [${hint}]: `)).trim().toLowerCase();
  if (answer === "") return fallback;
  return answer.startsWith("y");
}

/** Render a side-by-side field comparison. Shared by `label` and `review`. */
export function renderPair(
  rowA: Readonly<Record<string, unknown>>,
  rowB: Readonly<Record<string, unknown>>,
  columns: readonly string[],
  title: string,
): string {
  const cell = (v: unknown) => String(v ?? "").slice(0, 60);
  const width = Math.max(5, ...columns.map((c) => c.length));
  const lines = [title, "-".repeat(Math.max(title.length, 40))];
  for (const col of columns) {
    const a = cell(rowA[col]);
    const b = cell(rowB[col]);
    // Mark agreeing non-empty fields, the signal Python bolds.
    const same = a !== "" && a.toLowerCase() === b.toLowerCase();
    lines.push(`${col.padEnd(width)} | ${a.padEnd(60)} | ${b}${same ? "   =" : ""}`);
  }
  return lines.join("\n");
}
