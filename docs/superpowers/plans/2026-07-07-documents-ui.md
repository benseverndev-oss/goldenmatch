# Document Ingest Web UI Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A standalone `/documents` ("Ingest") React page: upload PDFs/images -> AI-suggest a schema -> review/edit it -> extract -> records table + CSV download, over the REST endpoints (#1533).

**Architecture:** A new `@tanstack/react-router` route + two components (`SchemaEditor`, `DocumentResults`) + two `lib/api.ts` methods (multipart) + a pure CSV builder. react-query mutations for the calls; react-table for results; Tailwind for styling. Frontend-only; the API is mocked in tests.

**Tech Stack:** React 19, TypeScript, Vite, @tanstack/react-router + react-query + react-table, Tailwind v4, vitest + @testing-library/react.

**Spec:** `docs/superpowers/specs/2026-07-07-documents-ui-design.md`

---

## Conventions for every task

- **Worktree:** `D:/show_case/gm-docs-ui`. Frontend root: `packages/python/goldenmatch/web/frontend`. Do NOT push, do NOT touch `main`.
- **CI IS THE SOURCE OF GREEN (read this).** This box is memory-starved: `vitest` and `vite build` frequently get OOM-killed (exit 137) (per the box-OOM memory). So:
  - Author every test as specified (TDD by construction), but when a run step says "run the test," try the SINGLE changed test file only: `npx vitest run src/__tests__/<file>.test.tsx`. If it completes, use its red/green. **If it dies with exit 137 / OOM, record that, do NOT fake a pass, and rely on CI** — the code is written correct-by-construction against the spec's contracts.
  - Prefer the cheapest local check: `npx tsc --noEmit` (typecheck) is lighter than `vite build`; run it after edits to catch type errors. `npx eslint <file>` for lint.
  - Do NOT run the full `npm test` / `npm run build` (they OOM). Never claim local green you did not actually observe.
- **Dep install (Task 1 prereq, exFAT-fiddly):** from the frontend root,
  `COREPACK_INTEGRITY_KEYS=0 pnpm install` (or `npm install` if pnpm isn't wired). If it fails on a workspace type package, see the ts-worktree-install reference (copy the missing `-types` package manually). If install itself OOMs/fails on this box, report it — the plan can still author all code; CI installs + runs.
- **Commits:** one per task; trailers copied from `git log -1 --format=%B`. `git -c commit.gpgsign=false commit`.

---

## File structure (locked, all under `web/frontend/src/`)

| path | responsibility |
|---|---|
| `lib/documentsCsv.ts` | pure `recordsToCsv(records, columns)` — CSV string builder (no DOM) |
| `lib/api.ts` (modify) | `postForm`, `suggestSchema`, `ingestDocuments` + the shared doc types |
| `components/SchemaEditor.tsx` | controlled field-row editor (name / kind / hint, add/remove) |
| `components/DocumentResults.tsx` | react-table of records + report summary + Download CSV |
| `routes/Documents.tsx` | the stepped page wiring upload -> suggest -> edit -> ingest -> results |
| `router.tsx` (modify) | register the `/documents` route + a `<NavLink to="/documents">Ingest</NavLink>` |
| `__tests__/documentsCsv.test.ts`, `SchemaEditor.test.tsx`, `DocumentResults.test.tsx`, `Documents.test.tsx` | tests |

---

## Task 1: API client + CSV builder (pure logic first)

**Files:** Create `lib/documentsCsv.ts`, `__tests__/documentsCsv.test.ts`; Modify `lib/api.ts`

- [ ] **Step 1: Failing test** — `src/__tests__/documentsCsv.test.ts`:
  ```ts
  import { describe, it, expect } from "vitest";
  import { recordsToCsv } from "../lib/documentsCsv";

  describe("recordsToCsv", () => {
    it("emits header + rows in column order, quoting commas/quotes", () => {
      const cols = ["full_name", "email", "_extract_confidence"];
      const rows = [
        { full_name: "Ada, L", email: 'a"@x.io', _extract_confidence: 0.9 },
        { full_name: "Bo", email: null, _extract_confidence: 0 },
      ];
      const csv = recordsToCsv(rows, cols);
      expect(csv).toBe(
        'full_name,email,_extract_confidence\r\n' +
        '"Ada, L","a""@x.io",0.9\r\n' +
        'Bo,,0\r\n'
      );
    });
  });
  ```
- [ ] **Step 2: Run** `npx vitest run src/__tests__/documentsCsv.test.ts` -> FAIL (module missing). (If OOM: note + continue; the test is authored.)
- [ ] **Step 3: Implement `lib/documentsCsv.ts`:**
  ```ts
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
  ```
- [ ] **Step 4: Run** the same test -> PASS (or CI).
- [ ] **Step 5: Add the API methods to `lib/api.ts`** — near the existing `post`/`json` helpers, add the doc types + methods (the `files` key is CRITICAL — repeated, no brackets):
  ```ts
  export type FieldKind = "text" | "email" | "phone" | "address" | "date" | "number";
  export type SchemaField = { name: string; kind: FieldKind; hint: string | null };
  export type TargetSchema = { fields: SchemaField[] };
  export type IngestReport = {
    n_files: number; n_rows: number; errors: { file: string; error: string }[];
  };
  export type IngestResult = { records: Record<string, unknown>[]; report: IngestReport };

  const postForm = <T>(path: string, form: FormData): Promise<T> =>
    fetch(path, { method: "POST", body: form }).then((r) => json<T>(r));
  ```
  Then add to the exported `api` object:
  ```ts
    suggestSchema: (file: File): Promise<{ schema: TargetSchema }> => {
      const form = new FormData();
      form.append("file", file);
      return postForm("/api/v1/documents/suggest-schema", form);
    },
    ingestDocuments: (files: File[], schema: TargetSchema): Promise<IngestResult> => {
      const form = new FormData();
      for (const f of files) form.append("files", f); // repeated key, NO brackets
      form.append("schema", JSON.stringify(schema));
      return postForm("/api/v1/documents/ingest", form);
    },
  ```
  Run `npx tsc --noEmit` to typecheck (lighter than build); fix any type errors.
- [ ] **Step 6: Commit** `feat(ui): documents api client methods + CSV builder`.

---

## Task 2: `SchemaEditor` component

**Files:** Create `components/SchemaEditor.tsx`, `__tests__/SchemaEditor.test.tsx`

- [ ] **Step 1: Failing test** — controlled editor renders fields, add/remove/edit call `onChange`:
  ```tsx
  import { describe, it, expect, vi } from "vitest";
  import { render, screen, fireEvent } from "@testing-library/react";
  import { SchemaEditor } from "../components/SchemaEditor";
  import type { TargetSchema } from "../lib/api";

  const schema: TargetSchema = { fields: [{ name: "full_name", kind: "text", hint: null }] };

  describe("SchemaEditor", () => {
    it("renders a row per field and adds a field", () => {
      const onChange = vi.fn();
      render(<SchemaEditor schema={schema} onChange={onChange} />);
      expect(screen.getByDisplayValue("full_name")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /add field/i }));
      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ fields: expect.arrayContaining([
          expect.objectContaining({ name: "full_name" }),
          expect.objectContaining({ name: "" }),
        ]) }));
    });
  });
  ```
- [ ] **Step 2: Run** `npx vitest run src/__tests__/SchemaEditor.test.tsx` -> FAIL (or OOM -> note).
- [ ] **Step 3: Implement `components/SchemaEditor.tsx`** — a controlled component (`{ schema, onChange }`): map each field to a row with a `name` text input, a `kind` `<select>` (the 6 `FieldKind`s), a `hint` text input, and a remove button; an "Add field" button appends `{ name: "", kind: "text", hint: null }`. Each edit builds a new `TargetSchema` and calls `onChange`. Tailwind classes consistent with existing components (look at `components/RuleEditor.tsx` for the row/input styling idiom). Keep it a pure controlled component (no internal fetch/state beyond the parent's `schema`).
- [ ] **Step 4: Run** -> PASS (or CI). `npx tsc --noEmit`.
- [ ] **Step 5: Commit** `feat(ui): SchemaEditor field-row component`.

---

## Task 3: `DocumentResults` component

**Files:** Create `components/DocumentResults.tsx`, `__tests__/DocumentResults.test.tsx`

- [ ] **Step 1: Failing test** — renders the record rows, the report summary, and a working Download:
  ```tsx
  import { describe, it, expect, vi } from "vitest";
  import { render, screen } from "@testing-library/react";
  import { DocumentResults } from "../components/DocumentResults";
  import type { IngestResult } from "../lib/api";

  const result: IngestResult = {
    records: [{ full_name: "Ada", email: "a@x.io", _extract_confidence: 0.9,
                _source_file: "a.png", _source_page: 0 }],
    report: { n_files: 1, n_rows: 1, errors: [] },
  };

  describe("DocumentResults", () => {
    it("shows records + summary; table omits _source_* columns", () => {
      render(<DocumentResults result={result} schemaColumns={["full_name", "email"]} />);
      expect(screen.getByText("Ada")).toBeInTheDocument();
      expect(screen.getByText(/1 record/i)).toBeInTheDocument();     // summary
      // table shows _extract_confidence but not _source_file
      expect(screen.queryByText("_source_file")).not.toBeInTheDocument();
    });
  });
  ```
- [ ] **Step 2: Run** -> FAIL (or OOM -> note).
- [ ] **Step 3: Implement `components/DocumentResults.tsx`** — props `{ result: IngestResult; schemaColumns: string[] }`. Table columns = `schemaColumns` + `_extract_confidence` (NOT `_source_file`/`_source_page`). Use `@tanstack/react-table` (mirror the existing table usage in `routes/Match.tsx`/`Compare.tsx`) or a plain `<table>` if simpler for v1 — but match the app's table idiom. Render a summary line (`{n_rows} record(s) from {n_files} file(s)`) and, if `report.errors.length`, a list of `{file}: {error}`. A **Download CSV** button builds `recordsToCsv(result.records, [...schemaColumns, "_source_file", "_source_page", "_extract_confidence"])` and triggers a client download (`Blob` + object URL + a temporary `<a>`; guard `document` for jsdom — the download click can be a no-op under test, just assert the button exists + the CSV builder is unit-tested separately in Task 1).
- [ ] **Step 4: Run** -> PASS (or CI). `npx tsc --noEmit`.
- [ ] **Step 5: Commit** `feat(ui): DocumentResults table + report + CSV download`.

---

## Task 4: `Documents` route (the flow)

**Files:** Create `routes/Documents.tsx`, `__tests__/Documents.test.tsx`

- [ ] **Step 1: Failing test** — mock `lib/api`, drive upload -> suggest -> extract -> results:
  ```tsx
  import { describe, it, expect, vi, beforeEach } from "vitest";
  import { render, screen, fireEvent, waitFor } from "@testing-library/react";
  import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
  import { Documents } from "../routes/Documents";

  vi.mock("../lib/api", () => ({
    api: {
      suggestSchema: vi.fn().mockResolvedValue({ schema: { fields: [
        { name: "full_name", kind: "text", hint: null }] } }),
      ingestDocuments: vi.fn().mockResolvedValue({
        records: [{ full_name: "Ada", _extract_confidence: 0.9 }],
        report: { n_files: 1, n_rows: 1, errors: [] } }),
    },
  }));

  const wrap = (ui: React.ReactNode) =>
    <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>;

  describe("Documents route", () => {
    it("upload -> suggest -> extract -> results", async () => {
      render(wrap(<Documents />));
      const file = new File([new Uint8Array([1])], "a.png", { type: "image/png" });
      const input = screen.getByLabelText(/upload|files/i) as HTMLInputElement;
      fireEvent.change(input, { target: { files: [file] } });
      fireEvent.click(screen.getByRole("button", { name: /suggest/i }));
      await waitFor(() => expect(screen.getByDisplayValue("full_name")).toBeInTheDocument());
      fireEvent.click(screen.getByRole("button", { name: /extract/i }));
      await waitFor(() => expect(screen.getByText("Ada")).toBeInTheDocument());
    });
  });
  ```
- [ ] **Step 2: Run** -> FAIL (or OOM -> note).
- [ ] **Step 3: Implement `routes/Documents.tsx`** — `export function Documents()`: page-local `useState` for `files: File[]` and `schema: TargetSchema | null`; a file `<input type="file" multiple>` (with an accessible label matching `/upload|files/i`); a `useMutation` for `api.suggestSchema(files[0])` whose `onSuccess` sets `schema` to `data.schema`; render `<SchemaEditor schema={schema} onChange={setSchema} />` once a schema exists; an "Extract" `useMutation` for `api.ingestDocuments(files, schema)`; render `<DocumentResults result={ingest.data} schemaColumns={schema.fields.map(f=>f.name)} />` when it resolves. Show `isPending` spinners + `error` banners (mirror `routes/Match.tsx`'s mutation + error-banner idiom). Disable Suggest/Extract when no files / no schema.
- [ ] **Step 4: Run** -> PASS (or CI). `npx tsc --noEmit`.
- [ ] **Step 5: Commit** `feat(ui): Documents ingest route (upload -> suggest -> extract -> results)`.

---

## Task 5: Register route + nav link

**Files:** Modify `router.tsx`; Test `__tests__/Documents.test.tsx` (add a registration assertion) or a small router smoke.

- [ ] **Step 1: Failing test** — the router exposes `/documents`:
  ```tsx
  import { describe, it, expect } from "vitest";
  import { router } from "../router";

  describe("router", () => {
    it("registers /documents", () => {
      const paths = router.flatRoutes.map((r) => r.fullPath);
      expect(paths).toContain("/documents");
    });
  });
  ```
  (If `router` isn't exported or `flatRoutes` differs, adapt to the real router API — read `router.tsx`; the assertion just needs to prove the route is in the tree.)
- [ ] **Step 2: Run** -> FAIL (or OOM -> note).
- [ ] **Step 3: Implement** — in `router.tsx`: `import { Documents } from "./routes/Documents";`; add
  ```ts
  const documentsRoute = createRoute({
    getParentRoute: () => rootRoute, path: "/documents", component: Documents,
  });
  ```
  add `documentsRoute` to the `rootRoute.addChildren([...])` array; and add `<NavLink to="/documents">Ingest</NavLink>` to the header `<nav>` (with the other NavLinks).
- [ ] **Step 4: Run** -> PASS (or CI). `npx tsc --noEmit` (the whole router typechecks).
- [ ] **Step 5: Commit** `feat(ui): register /documents route + Ingest nav link`.

---

## Task 6: Final verification (CI-first)

- [ ] **Step 1:** `npx tsc --noEmit` on the frontend (typecheck the whole app) — must be clean. This is the cheapest whole-app check that fits in memory; if even this OOMs, report it.
- [ ] **Step 2:** `npx eslint src/routes/Documents.tsx src/components/SchemaEditor.tsx src/components/DocumentResults.tsx src/lib/documentsCsv.ts` — clean.
- [ ] **Step 3:** Attempt `npx vitest run src/__tests__/documentsCsv.test.ts src/__tests__/SchemaEditor.test.tsx src/__tests__/DocumentResults.test.tsx src/__tests__/Documents.test.tsx`. If it completes: all green. **If OOM (137): record it and note that CI is the verifier** — the branch's real green is the TS build/test lane on the PR.
- [ ] **Step 4: Commit** any lint/type fixes: `chore(ui): typecheck + lint fixes for documents ingest`.

---

## Done-when

- `/documents` route renders the upload -> suggest -> edit -> extract -> results flow; "Ingest" nav link present.
- `api.ingestDocuments` posts files under the repeated `files` key (NOT `files[]`) + `schema` JSON.
- Table shows schema columns + `_extract_confidence`; CSV export includes all three sidecars.
- `tsc --noEmit` + eslint clean; the vitest suite green **on CI** (local may OOM — never faked).
- Deferred: dedupe handoff (persist-dataset), async/progress, field drag-reorder.
