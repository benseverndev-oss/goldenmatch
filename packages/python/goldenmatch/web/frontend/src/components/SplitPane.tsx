import { useCallback, useEffect, useRef, useState } from "react";

type Props = {
  /** Storage key — split percentage is persisted to localStorage so the
   *  user's preferred ratio survives reloads. Each call site picks its own. */
  storageKey: string;
  /** Initial split percentage (left pane width as % of container). */
  defaultPct?: number;
  /** Clamp bounds — both expressed as % of container width. */
  minPct?: number;
  maxPct?: number;
  /** Two children: left rail, then right pane. Anything else is ignored. */
  children: [React.ReactNode, React.ReactNode];
};

/** Two-column resizable split with a 5px draggable handle.
 *
 *  Drag updates a CSS variable on the container, which avoids re-rendering
 *  the children on every pointer move. We persist the final value to
 *  localStorage on pointerup so a noisy intermediate state doesn't get saved.
 */
export function SplitPane({
  storageKey,
  defaultPct = 35,
  minPct = 18,
  maxPct = 70,
  children,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [pct, setPct] = useState<number>(() => {
    if (typeof window === "undefined") return defaultPct;
    const raw = localStorage.getItem(storageKey);
    if (!raw) return defaultPct;
    const parsed = Number(raw);
    if (!Number.isFinite(parsed)) return defaultPct;
    return Math.min(maxPct, Math.max(minPct, parsed));
  });
  const dragging = useRef(false);

  const onPointerDown = useCallback((e: React.PointerEvent) => {
    dragging.current = true;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }, []);

  const onPointerMove = useCallback(
    (e: React.PointerEvent) => {
      if (!dragging.current || !containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      const next = ((e.clientX - rect.left) / rect.width) * 100;
      const clamped = Math.min(maxPct, Math.max(minPct, next));
      setPct(clamped);
    },
    [minPct, maxPct],
  );

  const onPointerUp = useCallback(
    (e: React.PointerEvent) => {
      if (!dragging.current) return;
      dragging.current = false;
      try {
        (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
      } catch {
        // pointer may already be released; harmless
      }
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      try {
        localStorage.setItem(storageKey, String(pct));
      } catch {
        // private mode etc — fall through
      }
    },
    [storageKey, pct],
  );

  // Keyboard accessibility — Left/Right arrows on the focused handle nudge
  // the split by 2%. Home/End jump to bounds.
  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      let next = pct;
      if (e.key === "ArrowLeft") next = pct - 2;
      else if (e.key === "ArrowRight") next = pct + 2;
      else if (e.key === "Home") next = minPct;
      else if (e.key === "End") next = maxPct;
      else return;
      e.preventDefault();
      setPct(Math.min(maxPct, Math.max(minPct, next)));
    },
    [pct, minPct, maxPct],
  );

  // Persist on every pct change (also catches keyboard nudges).
  useEffect(() => {
    try {
      localStorage.setItem(storageKey, String(pct));
    } catch {
      // ignore
    }
  }, [storageKey, pct]);

  return (
    <div
      ref={containerRef}
      className="grid h-full"
      style={{
        gridTemplateColumns: `${pct}% 5px minmax(0, 1fr)`,
      }}
    >
      <div className="min-w-0 overflow-hidden">{children[0]}</div>
      <div
        role="separator"
        aria-orientation="vertical"
        aria-valuemin={minPct}
        aria-valuemax={maxPct}
        aria-valuenow={Math.round(pct)}
        tabIndex={0}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onKeyDown={onKeyDown}
        className="relative cursor-col-resize bg-ink-200/50 hover:bg-gold/70 focus:bg-gold focus:outline-none transition-colors group"
        title="Drag to resize · ←/→ keys nudge"
      >
        {/* Wider invisible hit-target for easier grabbing */}
        <div className="absolute inset-y-0 -left-2 -right-2" />
        {/* Subtle dot grip in the middle, gold on hover */}
        <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 flex flex-col gap-0.5 opacity-50 group-hover:opacity-100 transition-opacity">
          {[0, 1, 2].map((i) => (
            <span
              key={i}
              className="block h-0.5 w-0.5 rounded-full bg-ink-500 group-hover:bg-paper-50"
            />
          ))}
        </div>
      </div>
      <div className="min-w-0 overflow-hidden">{children[1]}</div>
    </div>
  );
}
