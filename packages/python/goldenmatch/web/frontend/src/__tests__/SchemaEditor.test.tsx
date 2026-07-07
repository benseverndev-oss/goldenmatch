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
