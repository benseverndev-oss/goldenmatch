import type { FieldKind, SchemaField, TargetSchema } from "../lib/api";

const FIELD_KINDS: FieldKind[] = ["text", "email", "phone", "address", "date", "number"];

type SchemaEditorProps = {
  schema: TargetSchema;
  onChange: (schema: TargetSchema) => void;
};

export function SchemaEditor({ schema, onChange }: SchemaEditorProps) {
  const updateField = (idx: number, patch: Partial<SchemaField>) => {
    const next = schema.fields.map((f, i) => (i === idx ? { ...f, ...patch } : f));
    onChange({ ...schema, fields: next });
  };

  const removeField = (idx: number) => {
    onChange({ ...schema, fields: schema.fields.filter((_, i) => i !== idx) });
  };

  const addField = () => {
    onChange({
      ...schema,
      fields: [...schema.fields, { name: "", kind: "text", hint: null }],
    });
  };

  return (
    <section>
      <header className="flex items-baseline justify-between mb-3">
        <p className="eyebrow">fields · {schema.fields.length}</p>
        <button
          type="button"
          className="btn btn-ghost !text-[11px] !uppercase tracking-eyebrow"
          onClick={addField}
        >
          + add field
        </button>
      </header>

      <div className="space-y-3">
        {schema.fields.length === 0 && (
          <div className="card px-4 py-6 text-center text-sm text-ink-400">
            No fields yet. Click <span className="text-ink-600">+ add field</span> to start.
          </div>
        )}

        {schema.fields.map((f, idx) => (
          <article key={idx} className="card px-4 py-4">
            <div className="grid grid-cols-12 gap-x-4 gap-y-3 items-end">
              <div className="col-span-4">
                <p className="eyebrow mb-1">name</p>
                <input
                  type="text"
                  value={f.name}
                  onChange={(e) => updateField(idx, { name: e.target.value })}
                  placeholder="e.g. full_name"
                  className="w-full"
                />
              </div>
              <div className="col-span-3">
                <p className="eyebrow mb-1">kind</p>
                <select
                  value={f.kind}
                  onChange={(e) => updateField(idx, { kind: e.target.value as FieldKind })}
                  className="w-full"
                >
                  {FIELD_KINDS.map((k) => (
                    <option key={k} value={k}>
                      {k}
                    </option>
                  ))}
                </select>
              </div>
              <div className="col-span-4">
                <p className="eyebrow mb-1">hint</p>
                <input
                  type="text"
                  value={f.hint ?? ""}
                  onChange={(e) => updateField(idx, { hint: e.target.value || null })}
                  placeholder="optional extraction hint"
                  className="w-full"
                />
              </div>
              <div className="col-span-1 flex justify-end">
                <button
                  type="button"
                  className="btn btn-ghost !text-[11px] !uppercase tracking-eyebrow hover:!text-red-700 hover:!border-red-300"
                  onClick={() => removeField(idx)}
                >
                  remove
                </button>
              </div>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
