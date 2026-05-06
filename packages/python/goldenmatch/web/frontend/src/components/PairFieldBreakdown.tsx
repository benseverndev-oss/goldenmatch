import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { Pair } from "../lib/types";

type Props = { pair: Pair };

const renderValue = (v: unknown) => {
  if (v == null) return "—";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
};

export function PairFieldBreakdown({ pair }: Props) {
  const [savedLabel, setSavedLabel] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: (label: "match" | "non_match") =>
      api.postLabel({
        row_id_a: pair.row_id_a,
        row_id_b: pair.row_id_b,
        label,
      }),
    onSuccess: (_data, variables) => {
      setSavedLabel(variables);
      setTimeout(() => setSavedLabel(null), 1000);
    },
  });

  return (
    <div className="border rounded p-3 mb-3 bg-white">
      <div className="flex items-center justify-between gap-2 mb-2">
        <div className="text-sm font-medium">
          {pair.row_id_a} → {pair.row_id_b}
        </div>
        <div className="text-sm text-gray-600">
          score {pair.score.toFixed(3)}
        </div>
      </div>
      <div className="flex gap-2 mb-2">
        <button
          type="button"
          onClick={() => mutation.mutate("match")}
          disabled={mutation.isPending}
          className="px-2 py-1 text-xs border rounded bg-green-50 hover:bg-green-100 disabled:opacity-50"
        >
          {savedLabel === "match" ? "saved" : "Label match"}
        </button>
        <button
          type="button"
          onClick={() => mutation.mutate("non_match")}
          disabled={mutation.isPending}
          className="px-2 py-1 text-xs border rounded bg-red-50 hover:bg-red-100 disabled:opacity-50"
        >
          {savedLabel === "non_match" ? "saved" : "Label non-match"}
        </button>
      </div>
      {mutation.error && (
        <div className="text-xs text-red-600 mb-2">
          {String(mutation.error)}
        </div>
      )}
      <details>
        <summary className="text-xs text-gray-600 cursor-pointer">
          Field breakdown
        </summary>
        <table className="w-full text-xs mt-2">
          <thead>
            <tr className="bg-gray-50">
              <th className="px-2 py-1 text-left">field</th>
              <th className="px-2 py-1 text-left">scorer</th>
              <th className="px-2 py-1 text-left">value_a</th>
              <th className="px-2 py-1 text-left">value_b</th>
              <th className="px-2 py-1 text-left">score</th>
              <th className="px-2 py-1 text-left">weight</th>
              <th className="px-2 py-1 text-left">diff_type</th>
            </tr>
          </thead>
          <tbody>
            {pair.fields.map((f, i) => (
              <tr key={i} className="border-t">
                <td className="px-2 py-1">{f.field}</td>
                <td className="px-2 py-1">{f.scorer}</td>
                <td className="px-2 py-1">{renderValue(f.value_a)}</td>
                <td className="px-2 py-1">{renderValue(f.value_b)}</td>
                <td className="px-2 py-1">{f.score.toFixed(3)}</td>
                <td className="px-2 py-1">{f.weight}</td>
                <td className="px-2 py-1">{f.diff_type}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}
