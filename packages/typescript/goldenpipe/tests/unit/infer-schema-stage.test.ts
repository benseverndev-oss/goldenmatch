import { describe, it, expect } from "vitest";
import { makePipeContext, StageStatus, type Row } from "../../src/core/models.js";
import { InferSchemaStage } from "../../src/core/adapters/infer.js";
import type { InferredSchema } from "goldencheck-types";

// Same columns/values as the Python test's _ctx() -> detects "finance".
const FINANCE_ROWS: Row[] = [
  { account_number: "A1234", currency: "USD" },
  { account_number: "A5678", currency: "EUR" },
];

describe("infer_schema stage (InferMap port)", () => {
  it("auto-detects the finance domain", async () => {
    const ctx = makePipeContext({ df: FINANCE_ROWS });
    const result = await InferSchemaStage.run(ctx);
    expect(result.status).toBe(StageStatus.SUCCESS);
    const inferred = ctx.artifacts["inferred_schema"] as InferredSchema | null;
    expect(inferred).not.toBeNull();
    expect(inferred!.domain).toBe("finance");
  });

  it("honors an explicit domain", async () => {
    const ctx = makePipeContext({ df: FINANCE_ROWS, stageConfig: { domain: "finance" } });
    await InferSchemaStage.run(ctx);
    expect((ctx.artifacts["inferred_schema"] as InferredSchema).domain).toBe("finance");
  });

  it("no_infer returns null", async () => {
    const ctx = makePipeContext({ df: FINANCE_ROWS, stageConfig: { no_infer: true } });
    await InferSchemaStage.run(ctx);
    expect(ctx.artifacts["inferred_schema"]).toBeNull();
  });

  it("passes a user-provided schema through unchanged", async () => {
    const user: InferredSchema = { domain: "user", fields: {}, confidence: 1.0 };
    const ctx = makePipeContext({ df: FINANCE_ROWS, stageConfig: { schema: user } });
    await InferSchemaStage.run(ctx);
    expect(ctx.artifacts["inferred_schema"]).toBe(user);
  });

  it("throws on conflicting schema + domain", async () => {
    const user: InferredSchema = { domain: "user", fields: {}, confidence: 1.0 };
    const ctx = makePipeContext({ stageConfig: { schema: user, domain: "finance" } });
    await expect(InferSchemaStage.run(ctx)).rejects.toThrow(/conflict/);
  });

  it("throws on conflicting no_infer + domain", async () => {
    const ctx = makePipeContext({ stageConfig: { no_infer: true, domain: "finance" } });
    await expect(InferSchemaStage.run(ctx)).rejects.toThrow(/conflict/);
  });

  it("throws on conflicting no_infer + schema", async () => {
    const user: InferredSchema = { domain: "user", fields: {}, confidence: 1.0 };
    const ctx = makePipeContext({ stageConfig: { no_infer: true, schema: user } });
    await expect(InferSchemaStage.run(ctx)).rejects.toThrow(/conflict/);
  });
});
