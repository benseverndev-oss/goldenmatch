import { useParams } from "@tanstack/react-router";
import { RunInspector } from "../components/RunInspector";

export function Inspector() {
  const { name } = useParams({ strict: false }) as { name: string };
  return (
    <div className="p-4 h-[calc(100vh-3.5rem)]">
      <RunInspector name={name} />
    </div>
  );
}
