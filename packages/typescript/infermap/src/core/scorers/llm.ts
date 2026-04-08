// LLM scorer — pluggable adapter. Default implementation abstains (returns null),
// matching the Python stub in infermap/scorers/llm.py.
//
// Callers can construct an LLMScorer with an adapter function:
//
//   const scorer = new LLMScorer({
//     weight: 0.8,
//     adapter: async (prompt) => openai.chat.completions.create({ ... }),
//   });
//
// The adapter is invoked per (source, target) pair. Because adapters are async
// but the Scorer interface is sync (to match the Python Protocol), this
// default scorer is *not* wired into the sync map pipeline. Use the engine's
// async `mapWithLLM` path (future step) to include it. For now, this file
// ports the stub and exposes the adapter type for future work.
import type { FieldInfo, ScorerResult } from "../types.js";
import type { Scorer } from "./base.js";

export type LLMAdapter = (prompt: string) => Promise<string>;

export interface LLMScorerOptions {
  weight?: number;
  adapter?: LLMAdapter;
}

export class LLMScorer implements Scorer {
  readonly name = "LLMScorer";
  readonly weight: number;
  readonly adapter: LLMAdapter | undefined;

  constructor(options: LLMScorerOptions = {}) {
    this.weight = options.weight ?? 0.8;
    this.adapter = options.adapter;
  }

  // Sync interface — always abstains. The async path lives on the engine
  // and is introduced in Step 10+ when needed.
  score(_source: FieldInfo, _target: FieldInfo): ScorerResult | null {
    return null;
  }
}
