import type {
  BlockingKey,
  BlockingPayload,
  Matchkey,
  PydanticError,
  RulesPayload,
} from "../lib/types";
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
      <BlockingEditor rules={rules} onChange={onChange} errors={errors} />
    </div>
  );
}

function BlockingEditor({
  rules,
  onChange,
  errors,
}: {
  rules: RulesPayload;
  onChange: (rules: RulesPayload) => void;
  errors: PydanticError[];
}) {
  const blocking = rules.blocking;
  const blockingErrors = fieldErrorsFor(errors, ["body", "blocking"]);

  // Three modes the workbench actually surfaces. Anything else (ann, canopy,
  // learned, sorted_neighborhood) round-trips read-only so we don't clobber
  // a hand-tuned YAML the user has already invested in.
  const mode: "auto" | "static" | "multi_pass" | "other" = (() => {
    if (!blocking) return "auto";
    if (blocking.strategy === "multi_pass") return "multi_pass";
    if (blocking.strategy === "static" || blocking.strategy === undefined) {
      return blocking.auto_suggest ? "auto" : "static";
    }
    return "other";
  })();

  const setMode = (m: "auto" | "static" | "multi_pass") => {
    if (m === "auto") {
      onChange({ ...rules, blocking: null });
      return;
    }
    if (m === "static") {
      onChange({
        ...rules,
        blocking: {
          strategy: "static",
          keys: blocking?.keys?.length ? blocking.keys : [{ fields: [""], transforms: [] }],
          ...(blocking?.max_block_size !== undefined ? { max_block_size: blocking.max_block_size } : {}),
        },
      });
      return;
    }
    // multi_pass
    onChange({
      ...rules,
      blocking: {
        strategy: "multi_pass",
        // multi_pass requires keys OR passes; reuse existing keys as the
        // first pass when transitioning so the editor isn't empty.
        keys: blocking?.keys?.length ? blocking.keys : [{ fields: [""], transforms: [] }],
        passes:
          blocking?.passes?.length
            ? blocking.passes
            : blocking?.keys?.length
              ? blocking.keys
              : [{ fields: [""], transforms: [] }],
      },
    });
  };

  const updateBlocking = (patch: Partial<BlockingPayload>) => {
    onChange({ ...rules, blocking: { ...(blocking ?? {}), ...patch } });
  };

  return (
    <section>
      <header className="flex items-baseline justify-between mb-3">
        <p className="eyebrow">blocking</p>
        <div className="flex gap-1.5">
          {(["auto", "static", "multi_pass"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setMode(m)}
              disabled={mode === "other"}
              className={
                "font-mono text-[11px] px-2 py-1 border rounded transition-colors " +
                (mode === m
                  ? "border-gold-400 text-gold-600 bg-gold-100"
                  : "border-ink-200 text-ink-500 hover:border-ink-500 hover:text-ink-700")
              }
              title={mode === "other" ? "advanced strategy in YAML — edit by hand" : undefined}
            >
              {m}
            </button>
          ))}
        </div>
      </header>

      <p className="text-[11px] text-ink-500 mb-3 max-w-prose">
        Blocking decides which pairs are even compared. <code className="font-mono">auto</code> lets
        the engine pick keys per-column. <code className="font-mono">static</code> pins exact keys.
        <code className="font-mono"> multi_pass</code> runs several blocking passes (good for catching
        phonetic + email + substring duplicates in one config).
      </p>

      {mode === "other" && (
        <div className="card px-4 py-3 text-sm text-ink-700">
          Strategy <code className="font-mono text-gold-600">{blocking?.strategy}</code> isn't
          surfaced in the workbench. Edit <code className="font-mono">goldenmatch.yml</code> directly
          to tune it; saving here will preserve the existing block.
        </div>
      )}

      {mode === "auto" && (
        <div className="card px-4 py-3 text-sm text-ink-500">
          Engine will discover blocking keys at runtime via <code className="font-mono">auto_suggest</code>.
          Switch to <code className="font-mono">static</code> or <code className="font-mono">multi_pass</code> to pin them.
        </div>
      )}

      {(mode === "static" || mode === "multi_pass") && (
        <div className="space-y-3">
          <KeyList
            label={mode === "multi_pass" ? "keys (fallback)" : "keys"}
            keys={blocking?.keys ?? []}
            onChange={(keys) => updateBlocking({ keys })}
          />
          {mode === "multi_pass" && (
            <KeyList
              label="passes"
              keys={blocking?.passes ?? []}
              onChange={(passes) => updateBlocking({ passes })}
            />
          )}
          <div className="card px-4 py-3 grid grid-cols-2 gap-4">
            <label>
              <p className="eyebrow mb-1">max block size</p>
              <input
                type="number"
                min={1}
                value={blocking?.max_block_size ?? 5000}
                onChange={(e) =>
                  updateBlocking({ max_block_size: Math.max(1, Number(e.target.value) || 5000) })
                }
                className="w-full"
              />
            </label>
            <label className="flex items-center gap-2 self-end pb-2">
              <input
                type="checkbox"
                checked={!!blocking?.skip_oversized}
                onChange={(e) => updateBlocking({ skip_oversized: e.target.checked })}
              />
              <span className="text-sm text-ink-700">skip oversized blocks</span>
            </label>
          </div>
        </div>
      )}
      <ErrorList messages={blockingErrors} />
    </section>
  );
}

function KeyList({
  label,
  keys,
  onChange,
}: {
  label: string;
  keys: BlockingKey[];
  onChange: (keys: BlockingKey[]) => void;
}) {
  const updateKey = (idx: number, patch: Partial<BlockingKey>) => {
    onChange(keys.map((k, i) => (i === idx ? { ...k, ...patch } : k)));
  };
  const removeKey = (idx: number) => onChange(keys.filter((_, i) => i !== idx));
  const addKey = () => onChange([...keys, { fields: [""], transforms: [] }]);

  return (
    <div>
      <header className="flex items-baseline justify-between mb-2">
        <p className="eyebrow">{label} · {keys.length}</p>
        <button
          type="button"
          className="btn btn-ghost !text-[11px] !uppercase tracking-eyebrow"
          onClick={addKey}
        >
          + add key
        </button>
      </header>
      <div className="space-y-2">
        {keys.map((k, idx) => (
          <article key={idx} className="card px-4 py-3">
            <div className="flex items-baseline gap-3 mb-3">
              <span className="num text-[11px] text-ink-400 tabular-nums">
                {String(idx + 1).padStart(2, "0")}
              </span>
              <span className="eyebrow">fields</span>
              <input
                type="text"
                value={k.fields.join(", ")}
                onChange={(e) =>
                  updateKey(idx, {
                    fields: e.target.value
                      .split(",")
                      .map((s) => s.trim())
                      .filter(Boolean),
                  })
                }
                placeholder="e.g. last_name, zip"
                className="flex-1"
              />
              <button
                type="button"
                className="btn btn-ghost !text-[11px] !uppercase tracking-eyebrow hover:!text-red-700 hover:!border-red-300"
                onClick={() => removeKey(idx)}
              >
                remove
              </button>
            </div>
            <div>
              <p className="eyebrow mb-2">transforms</p>
              <div className="flex flex-wrap gap-1.5">
                {TRANSFORMS.map((t) => {
                  const active = k.transforms.includes(t);
                  return (
                    <button
                      key={t}
                      type="button"
                      onClick={() =>
                        updateKey(idx, {
                          transforms: active
                            ? k.transforms.filter((x) => x !== t)
                            : [...k.transforms, t],
                        })
                      }
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
            </div>
          </article>
        ))}
      </div>
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
