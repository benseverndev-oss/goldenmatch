import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { RuleEditor } from "../components/RuleEditor";
import type { RulesPayload } from "../lib/types";

const baseRules: RulesPayload = {
  threshold: 0.85,
  matchkeys: [
    { column: "name", scorer: "jaro_winkler", weight: 1.0, transforms: ["lowercase"] },
  ],
};

describe("RuleEditor", () => {
  it("renders the threshold and the matchkey row", () => {
    render(<RuleEditor rules={baseRules} onChange={() => {}} errors={[]} />);
    expect(screen.getByDisplayValue("name")).toBeInTheDocument();
    expect(screen.getByDisplayValue("jaro_winkler")).toBeInTheDocument();
  });

  it("calls onChange when scorer dropdown changes", () => {
    const onChange = vi.fn();
    render(<RuleEditor rules={baseRules} onChange={onChange} errors={[]} />);
    fireEvent.change(screen.getByDisplayValue("jaro_winkler"), {
      target: { value: "exact" },
    });
    expect(onChange).toHaveBeenCalled();
    const next = onChange.mock.calls[0][0] as RulesPayload;
    expect(next.matchkeys[0].scorer).toBe("exact");
  });

  it("renders a per-matchkey error from a model_validator (loc=[body, matchkeys, 0])", () => {
    render(
      <RuleEditor
        rules={baseRules}
        onChange={() => {}}
        errors={[
          {
            loc: ["body", "matchkeys", 0],
            msg: "MatchkeyField requires 'field' or 'column'",
          },
        ]}
      />,
    );
    expect(screen.getByText(/MatchkeyField requires/)).toBeInTheDocument();
  });
});
