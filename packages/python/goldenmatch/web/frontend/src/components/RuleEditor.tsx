import type { PydanticError, RulesPayload, Matchkey } from "../lib/types";
import { SCORERS, TRANSFORMS } from "../lib/types";

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
    <div className="text-xs text-red-600 mt-1">
      {messages.map((m, i) => (
        <div key={i}>{m}</div>
      ))}
    </div>
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
    <div className="space-y-4">
      <div>
        <label className="block text-sm font-semibold mb-1">Threshold</label>
        <div className="flex items-center gap-3">
          <input
            type="range"
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
            className="w-20 border rounded px-2 py-1 text-sm"
            aria-label="threshold number"
          />
          <span className="text-xs text-gray-500 w-12">
            {rules.threshold.toFixed(2)}
          </span>
        </div>
        <ErrorList messages={thresholdErrors} />
      </div>

      <div>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-semibold">Matchkeys</h3>
          <button
            type="button"
            className="px-2 py-1 text-xs border rounded hover:bg-gray-50"
            onClick={addMatchkey}
          >
            + Add matchkey
          </button>
        </div>

        <div className="space-y-3">
          {rules.matchkeys.map((mk, idx) => {
            const rowErrors = fieldErrorsFor(errors, [
              "body",
              "matchkeys",
              idx,
            ]);
            const colErrors = fieldErrorsFor(errors, [
              "body",
              "matchkeys",
              idx,
              "column",
            ]);
            const scorerErrors = fieldErrorsFor(errors, [
              "body",
              "matchkeys",
              idx,
              "scorer",
            ]);
            const weightErrors = fieldErrorsFor(errors, [
              "body",
              "matchkeys",
              idx,
              "weight",
            ]);
            const transformErrors = fieldErrorsFor(errors, [
              "body",
              "matchkeys",
              idx,
              "transforms",
            ]);
            return (
              <div
                key={idx}
                className="border rounded p-3 space-y-2 bg-white"
              >
                <div className="grid grid-cols-12 gap-2 items-start">
                  <div className="col-span-3">
                    <label className="block text-xs text-gray-500 mb-1">
                      Column
                    </label>
                    <input
                      type="text"
                      value={mk.column}
                      onChange={(e) =>
                        updateMatchkey(idx, { column: e.target.value })
                      }
                      className="w-full border rounded px-2 py-1 text-sm"
                    />
                    <ErrorList messages={colErrors} />
                  </div>
                  <div className="col-span-3">
                    <label className="block text-xs text-gray-500 mb-1">
                      Scorer
                    </label>
                    <select
                      value={mk.scorer}
                      onChange={(e) =>
                        updateMatchkey(idx, { scorer: e.target.value })
                      }
                      className="w-full border rounded px-2 py-1 text-sm"
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
                    <label className="block text-xs text-gray-500 mb-1">
                      Weight
                    </label>
                    <div className="flex items-center gap-2">
                      <input
                        type="range"
                        min={0}
                        max={1}
                        step={0.01}
                        value={mk.weight}
                        onChange={(e) =>
                          updateMatchkey(idx, {
                            weight: Number(e.target.value),
                          })
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
                          updateMatchkey(idx, {
                            weight: Number(e.target.value),
                          })
                        }
                        className="w-16 border rounded px-2 py-1 text-sm"
                        aria-label={`weight number ${idx}`}
                      />
                    </div>
                    <ErrorList messages={weightErrors} />
                  </div>
                  <div className="col-span-1 flex justify-end pt-5">
                    <button
                      type="button"
                      className="px-2 py-1 text-xs border rounded text-red-600 hover:bg-red-50"
                      onClick={() => removeMatchkey(idx)}
                    >
                      Remove
                    </button>
                  </div>
                </div>

                <div>
                  <label className="block text-xs text-gray-500 mb-1">
                    Transforms
                  </label>
                  {mk.transforms.length > 0 && (
                    <div className="flex flex-wrap gap-1 mb-2">
                      {mk.transforms.map((t) => (
                        <span
                          key={t}
                          className="text-xs bg-blue-100 text-blue-800 px-2 py-0.5 rounded"
                        >
                          {t}
                        </span>
                      ))}
                    </div>
                  )}
                  <div className="grid grid-cols-3 gap-1">
                    {TRANSFORMS.map((t) => (
                      <label
                        key={t}
                        className="flex items-center gap-1 text-xs"
                      >
                        <input
                          type="checkbox"
                          checked={mk.transforms.includes(t)}
                          onChange={() => toggleTransform(idx, t)}
                        />
                        {t}
                      </label>
                    ))}
                  </div>
                  <ErrorList messages={transformErrors} />
                </div>

                <ErrorList messages={rowErrors} />
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
