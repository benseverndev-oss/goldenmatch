# Document Ingest Web UI — design

**Goal.** A standalone `/documents` page ("Ingest") in the React workbench that walks a user through
upload -> AI-suggested schema -> review/edit -> extract -> records table + CSV download, calling the
document-ingest REST endpoints (PR #1533). First browser surface for document ingest.

**Builds on:** the REST endpoints `POST /api/v1/documents/{suggest-schema,ingest}` (#1533). This is a
frontend-only change (React/TS); it calls the endpoints over HTTP and mocks them in tests, so it has
NO code dependency on the Python router and branches independently off main.

**Stack (existing):** React 19 + TypeScript + Vite; `@tanstack/react-router` (routes in
`web/frontend/src/routes/`, registered in `router.tsx`); `@tanstack/react-query`;
`@tanstack/react-table`; Tailwind v4; typed API client `src/lib/api.ts`.

## Decisions (settled in brainstorming)

- **Standalone v1:** no dedupe handoff (persist-dataset is a later slice). The page extracts records
  and offers a CSV download; the user can feed that into dedupe manually for now.
- **Structured field editor** (not a raw-JSON textarea) for reviewing the AI-suggested schema.
- Route `/documents`, nav label **"Ingest"**.

## Components / files (all under `web/frontend/src/`)

| path | responsibility |
|---|---|
| `routes/Documents.tsx` | the stepped page: upload -> suggest -> edit -> ingest -> results |
| `components/SchemaEditor.tsx` | field rows (`name` + `kind` dropdown + `hint`, add/remove); controlled by parent state |
| `components/DocumentResults.tsx` | `react-table` of records + report summary + Download CSV |
| `lib/api.ts` (modify) | add `suggestSchema(file)` + `ingestDocuments(files, schema)` (multipart via a new `postForm` helper) |
| `router.tsx` (modify) | `createRoute({ path: "/documents", component: Documents })` into the tree + a `<NavLink to="/documents">Ingest</NavLink>` in the header nav |

## Types (mirror the REST contract)

```ts
type FieldKind = "text" | "email" | "phone" | "address" | "date" | "number";
type SchemaField = { name: string; kind: FieldKind; hint: string | null };
type TargetSchema = { fields: SchemaField[] };
type IngestReport = { n_files: number; n_rows: number; errors: { file: string; error: string }[] };
type IngestResult = { records: Record<string, unknown>[]; report: IngestReport };
```

## API client (`lib/api.ts`)

- `postForm<T>(path, form: FormData): Promise<T>` — `fetch(path, { method: "POST", body: form })`
  (NO `content-type` header — the browser sets the multipart boundary), reusing the existing `json<T>`
  error handling. Matches the existing client's no-auth-header pattern.
- `suggestSchema(file: File): Promise<{ schema: TargetSchema }>` -> POST `/api/v1/documents/suggest-schema`
  with `file`.
- `ingestDocuments(files: File[], schema: TargetSchema): Promise<IngestResult>` -> POST
  `/api/v1/documents/ingest`. **CRITICAL:** append each file under the repeated key `files` with NO
  brackets — `for (const f of files) form.append("files", f)`. FastAPI binds the repeated multipart
  field by the exact key `files` (`documents.py`: `files: list[UploadFile] | None = File(None)`); a
  `files[]` key (the common JS convention) makes the server see zero files and return `400 "no files
  uploaded"` on every request. The schema goes as a single JSON-string field:
  `form.append("schema", JSON.stringify(schema))`.

## Flow (single page)

1. **Upload** — a file input / dropzone accepting `.pdf,.png,.jpg,.jpeg,.tif,.tiff,.webp` (multiple).
2. **Suggest** — a "Suggest schema" action runs `suggestSchema(files[0])` (react-query mutation) and
   loads its fields into the editor. (User may also start editing an empty schema manually.)
3. **Edit** — `SchemaEditor` on the suggested fields: add/remove rows, rename, pick kind, edit hint.
4. **Extract** — an "Extract" action runs `ingestDocuments(files, schema)` (react-query mutation).
5. **Results** — `DocumentResults`: the records **table** shows the schema columns + `_extract_confidence`
   (the review-useful sidecar); it does NOT show `_source_file`/`_source_page` as columns (kept for
   provenance, off the review grid). The report summary (`n_rows` from `n_files`, plus per-file
   `errors`), and **Download CSV** exporting the schema columns + ALL three sidecars
   (`_source_file`, `_source_page`, `_extract_confidence`) so downstream `dedupe_df(exclude_columns=...)`
   has the full provenance.

## Data fetching + state

`useMutation` (react-query) for suggest + ingest, exposing `isPending`/`error`/`data` for spinners and
inline error banners through the seconds-long VLM latency. Page-local `useState` holds the selected
files and the working schema (the editor is controlled).

## Error / edge handling

- 400 from suggest/ingest (bad schema / missing key) -> the mutation `error` surfaces the endpoint's
  `detail` in an inline banner.
- Per-file extraction errors -> rendered from `report.errors` (partial success still shows the rows
  that succeeded).
- No files selected -> Extract/Suggest disabled.
- Empty records -> a friendly empty state.

## Testing (Vitest + @testing-library/react)

- Mock the API (mock `lib/api.ts` or `fetch`), NO live calls:
  - upload + Suggest -> editor renders the suggested fields;
  - edit a field + Extract -> `DocumentResults` renders the records + report;
  - a 400 error -> the inline error banner shows the detail;
  - Download CSV produces the expected header + rows (test the CSV builder as a pure function).
- Keep the CSV builder a pure function in `lib/` so it unit-tests without the DOM.

## Constraints that shape EXECUTION (not optional)

- **The box OOM-kills `vitest` / `vite build`** (memory-starved machine; TS builds get SIGKILL 137).
  So local verification is limited — **frontend typecheck/build/test verification leans on CI** (the
  `api_parity` / TS build lanes). The plan must NOT assume `npm test` / `vite build` run cleanly on
  this box; where a local check is needed, prefer `tsc --noEmit` on the single changed files or run
  the smallest possible test, and treat CI as the source of truth for green.
- **Frontend dep install on the exFAT worktree is fiddly** (corepack integrity): set
  `COREPACK_INTEGRITY_KEYS=0` and, if a workspace type package is missing, copy it manually (see the
  ts-worktree-install reference). The plan spells out the exact install incantation.

## Scope (YAGNI)

**In:** the `/documents` route + upload + `SchemaEditor` + `DocumentResults` + CSV download + the two
api client methods + nav link + component/unit tests. **Out:** the dedupe handoff (persist-dataset),
async/progress bars, field drag-reorder, multi-sample suggest, auth-header handling (matches the
existing UI's no-header pattern).

## Open risks

- **Local un-runnability:** because of the box OOM, the implementer may be unable to fully run the
  React test suite locally. Structure the plan so the pure-logic (CSV builder, api client shape) is
  unit-testable cheaply, the component tests are written to run in CI, and the branch's green is
  confirmed on CI before merge — do NOT claim local green if it wasn't actually run.
