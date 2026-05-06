import type { PydanticError, RulesPayload, Matchkey } from "../lib/types";
import { SCORERS, STANDARDIZERS, TRANSFORMS } from "../lib/types";

type RuleEditorProps = {
  rules: RulesPayload;
  onChange: (rules: RulesPayload) => void;
  errors: PydanticError[];
};

function fieldErrorsFor(
  errors: PydanticError[],
  path: (string | number)[],
): string[] {
  return errors
    .filter((e) => path.every((p, i) => e.loc[i] === p))
    .map((e) => e.msg);
}

function ErrorList({ messages }: { messages: string[] }) {
  if (messages.length === 0) return null;
  return (
    <div className="text-[11px] text-red-700 font-mono mt-1 space-y-0.5">
      {messages.map((m, i) => (
        <div key={i}>↳ {m}</div>
      ))}
    </div>
  );
}

/** A range input that paints its filled portion gold using --val (0-100). */
function GoldRange(props: React.InputHTMLAttributes<HTMLInputElement>) {
  const v = Number(props.value ?? 0);
  const min = Number(props.min ?? 0);
  const max = Number(props.max ?? 1);
  const pct = Math.max(0, Math.min(100, ((v - min) / (max - min)) * 100));
  return (
    <input
      type="range"
      {...props}
      style={
        { ...(props.style ?? {}), ["--val" as string]: `${pct}%` } as React.CSSProperties
      }
    />
  );
}

export function RuleEditor({ rules, onChange, errors }: RuleEditorProps) {
  const updateMatchkey = (idx: number, patch: Partial<Matchkey>) => {
    const next = rules.matchkeys.map((mk, i) =>
      i === idx ? { ...mk, ...patch } : mk,
    );
    onChange({ ...rules, matchkeys: next });
  };

  const removeMatchkey = (idx: number) => {
    onChange({
      ...rules,
      matchkeys: rules.matchkeys.filter((_, i) => i !== idx),
    });
  };

  const addMatchkey = () => {
    onChange({
      ...rules,
      matchkeys: [
        ...rules.matchkeys,
        { column: "", scorer: "exact", weight: 1.0, transforms: [] },
      ],
    });
  };

  const toggleTransform = (idx: number, t: string) => {
    const mk = rules.matchkeys[idx];
    if (!mk) return;
    const has = mk.transforms.includes(t);
    const next = has
      ? mk.transforms.filter((x) => x !== t)
      : [...mk.transforms, t];
    updateMatchkey(idx, { transforms: next });
  };

  const thresholdErrors = fieldErrorsFor(errors, ["body", "threshold"]);

  return (
    <div className="space-y-6">
      {/* Threshold */}
      <section>
        <p className="eyebrow mb-2">threshold</p>
        <div className="flex items-center gap-4">
          <GoldRange
            min={0}
            max={1}
            step={0.01}
            value={rules.threshold}
            onChange={(e) =>
              onChange({ ...rules, threshold: Number(e.target.value) })
            }
            className="flex-1"
            aria-label="threshold range"
          />
          <input
            type="number"
            min={0}
            max={1}
            step={0.01}
            value={rules.threshold}
            onChange={(e) =>
              onChange({ ...rules, threshold: Number(e.target.value) })
            }
            className="w-20 text-center"
            aria-label="threshold number"
          />
        </div>
        <ErrorList messages={thresholdErrors} />
      </section>

      {/* Matchkeys */}
      <section>
        <header className="flex items-baseline justify-between mb-3">
          <p className="eyebrow">matchkeys · {rules.matchkeys.length}</p>
          <button
            type="button"
            className="btn btn-ghost !text-[11px] !uppercase tracking-eyebrow"
            onClick={addMatchkey}
          >
            + add matchkey
          </button>
        </header>

        <div className="space-y-3">
          {rules.matchkeys.length === 0 && (
            <div className="card px-4 py-6 text-center text-sm text-ink-400">
              No matchkeys yet. Click <span className="text-ink-600">+ add matchkey</span> to start.
            </div>
          )}

          {rules.matchkeys.map((mk, idx) => {
            const rowErrors = fieldErrorsFor(errors, ["body", "matchkeys", idx]);
            const colErrors = fieldErrorsFor(errors, ["body", "matchkeys", idx, "column"]);
            const scorerErrors = fieldErrorsFor(errors, ["body", "matchkeys", idx, "scorer"]);
            const weightErrors = fieldErrorsFor(errors, ["body", "matchkeys", idx, "weight"]);
            const transformErrors = fieldErrorsFor(errors, ["body", "matchkeys", idx, "transforms"]);
            return (
              <article key={idx} className="card px-4 py-4 space-y-4">
                <div className="flex items-baseline gap-3">
                  <span className="num text-[11px] text-ink-400 tabular-nums">
                    {String(idx + 1).padStart(2, "0")}
                  </span>
                  <span className="eyebrow">matchkey</span>
                  <button
                    type="button"
                    className="ml-auto btn btn-ghost !text-[11px] !uppercase tracking-eyebrow hover:!text-red-700 hover:!border-red-300"
                    onClick={() => removeMatchkey(idx)}
                  >
                    remove
                  </button>
                </div>

                <div className="grid grid-cols-12 gap-x-4 gap-y-3">
                  <div className="col-span-4">
                    <p className="eyebrow mb-1">column</p>
                    <input
                      type="text"
                      value={mk.column}
                      onChange={(e) =>
                        updateMatchkey(idx, { column: e.target.value })
                      }
                      placeholder="e.g. name"
                      className="w-full"
                    />
                    <ErrorList messages={colErrors} />
                  </div>
                  <div className="col-span-3">
                    <p className="eyebrow mb-1">scorer</p>
                    <select
                      value={mk.scorer}
                      onChange={(e) =>
                        updateMatchkey(idx, { scorer: e.target.value })
                      }
                      className="w-full"
                    >
                      {SCORERS.map((s) => (
                        <option key={s} value={s}>
                          {s}
                        </option>
                      ))}
                    </select>
                    <ErrorList messages={scorerErrors} />
                  </div>
                  <div className="col-span-5">
                    <p className="eyebrow mb-1">weight</p>
                    <div className="flex items-center gap-3">
                      <GoldRange
                        min={0}
                        max={1}
                        step={0.01}
                        value={mk.weight}
                        onChange={(e) =>
                          updateMatchkey(idx, { weight: Number(e.target.value) })
                        }
                        className="flex-1"
                        aria-label={`weight range ${idx}`}
                      />
                      <input
                        type="number"
                        min={0}
                        max={1}
                        step={0.01}
                        value={mk.weight}
                        onChange={(e) =>
                          updateMatchkey(idx, { weight: Number(e.target.value) })
                        }
                        className="w-16 text-center"
                        aria-label={`weight number ${idx}`}
                      />
                    </div>
                    <ErrorList messages={weightErrors} />
                  </div>
                </div>

                <div>
                  <p className="eyebrow mb-2">transforms</p>
                  <div className="flex flex-wrap gap-1.5">
                    {TRANSFORMS.map((t) => {
                      const active = mk.transforms.includes(t);
                      return (
                        <button
                          key={t}
                          type="button"
                          onClick={() => toggleTransform(idx, t)}
                          className={
                            "font-mono text-[11px] px-2 py-1 border rounded transition-colors " +
                            (active
                              ? "border-gold-400 text-gold-600 bg-gold-100"
                              : "border-ink-200 text-ink-500 hover:border-ink-500 hover:text-ink-700")
                          }
                        >
                          {t}
                        </button>
                      );
                    })}
                  </div>
                  <ErrorList messages={transformErrors} />
                </div>

                <ErrorList messages={rowErrors} />
              </article>
            );
          })}
        </div>
      </section>

      <StandardizationEditor rules={rules} onChange={onChange} errors={errors} />
    </div>
  );
}

function StandardizationEditor({
  rules,
  onChange,
  errors,
}: {
  rules: RulesPayload;
  onChange: (rules: RulesPayload) => void;
  errors: PydanticError[];
}) {
  const std = rules.standardization ?? {};
  const entries = Object.entries(std);
  const stdErrors = fieldErrorsFor(errors, ["body", "standardization"]);

  const setEntries = (next: [string, string[]][]) => {
    const obj: Record<string, string[]> = {};
    for (const [col, names] of next) {
      // Drop empty rows on serialize so the payload stays clean.
      if (col.trim()) obj[col] = names;
    }
    onChange({
      ...rules,
      standardization: Object.keys(obj).length ? obj : null,
    });
  };

  const updateColumn = (idx: number, column: string) => {
    const next = entries.map(([c, n], i): [string, string[]] =>
      i === idx ? [column, n] : [c, n],
    );
    setEntries(next);
  };

  const toggleStandardizer = (idx: number, name: string) => {
    const next = entries.map(([c, n], i): [string, string[]] => {
      if (i !== idx) return [c, n];
      return [c, n.includes(name) ? n.filter((x) => x !== name) : [...n, name]];
    });
    setEntries(next);
  };

  const removeRow = (idx: number) => {
    setEntries(entries.filter((_, i) => i !== idx));
  };

  const addRow = () => {
    setEntries([...entries, ["", []]]);
  };

  return (
    <section>
      <header className="flex items-baseline justify-between mb-3">
        <p className="eyebrow">standardization · {entries.length}</p>
        <button
          type="button"
          className="btn btn-ghost !text-[11px] !uppercase tracking-eyebrow"
          onClick={addRow}
        >
          + add column
        </button>
      </header>

      <p className="text-[11px] text-ink-500 mb-3 max-w-prose">
        Run before matchkey scoring. Useful for normalizing variants that the
        scorer would otherwise see as different — e.g. <code className="font-mono">name_proper</code> on a name column collapses
        ALL CAPS / mixed case before string similarity runs.
      </p>

      <div className="space-y-3">
        {entries.length === 0 && (
          <div className="card px-4 py-6 text-center text-sm text-ink-400">
            No standardizers configured. Click <span className="text-ink-600">+ add column</span> to start.
          </div>
        )}

        {entries.map(([column, names], idx) => (
          <article key={idx} className="card px-4 py-4 space-y-3">
            <div className="flex items-baseline gap-3">
              <span className="num text-[11px] text-ink-400 tabular-nums">
                {String(idx + 1).padStart(2, "0")}
              </span>
              <span className="eyebrow">column</span>
              <input
                type="text"
                value={column}
                onChange={(e) => updateColumn(idx, e.target.value)}
                placeholder="e.g. name"
                className="flex-1 max-w-xs"
              />
              <button
                type="button"
                className="ml-auto btn btn-ghost !text-[11px] !uppercase tracking-eyebrow hover:!text-red-700 hover:!border-red-300"
                onClick={() => removeRow(idx)}
              >
                remove
              </button>
            </div>

            <div>
              <p className="eyebrow mb-2">standardizers · applied in order</p>
              <div className="flex flex-wrap gap-1.5">
                {STANDARDIZERS.map((s) => {
                  const active = names.includes(s);
                  return (
                    <button
                      key={s}
                      type="button"
                      onClick={() => toggleStandardizer(idx, s)}
                      className={
                        "font-mono text-[11px] px-2 py-1 border rounded transition-colors " +
                        (active
                          ? "border-gold-400 text-gold-600 bg-gold-100"
                          : "border-ink-200 text-ink-500 hover:border-ink-500 hover:text-ink-700")
                      }
                    >
                      {s}
                    </button>
                  );
                })}
              </div>
            </div>
          </article>
        ))}
      </div>

      <ErrorList messages={stdErrors} />
    </section>
  );
}
