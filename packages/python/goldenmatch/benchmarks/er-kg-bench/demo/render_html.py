"""Pure HTML rendering for the ER-KG demo. No network, no goldenmatch. Renders a
snapshot dict (see run_demo.build_snapshot) into one self-contained .html string."""
from __future__ import annotations

from html import escape

# Width / height constants for the SVG nodes view.
_SVG_W = 480
_NODE_H = 60
_NODE_W = 420
_NODE_X = 30
_NODE_GAP = 16
_FONT = "14"
_LABEL_FONT = "11"


def _svg_nodes(nodes: list[dict], *, title: str) -> str:
    """One rounded rect per node, stacked vertically; the node's names listed inside.
    Deterministic layout (positions derived from index). Returns an <svg>...</svg> string."""
    n = len(nodes)
    total_h = max(n * (_NODE_H + _NODE_GAP) + _NODE_GAP + 28, 80)
    parts: list[str] = []
    parts.append(
        f'<svg width="{_SVG_W}" height="{total_h}" '
        f'role="img" aria-label="{escape(title)}">'
    )
    # Title text at top
    parts.append(
        f'<text x="{_SVG_W // 2}" y="18" text-anchor="middle" '
        f'font-size="{_LABEL_FONT}" fill="#555" font-family="sans-serif">'
        f'{escape(title)}</text>'
    )
    for idx, node in enumerate(nodes):
        y = 28 + idx * (_NODE_H + _NODE_GAP)
        names: list[str] = node.get("names", [])
        node_type = node.get("type", "")
        # Rect
        parts.append(
            f'<rect x="{_NODE_X}" y="{y}" width="{_NODE_W}" height="{_NODE_H}" '
            f'rx="8" ry="8" fill="#e8f0fe" stroke="#4a80d4" stroke-width="1.5"/>'
        )
        # Primary name (first)
        primary = names[0] if names else "(unnamed)"
        parts.append(
            f'<text x="{_NODE_X + 12}" y="{y + 22}" '
            f'font-size="{_FONT}" font-weight="bold" fill="#1a1a2e" font-family="sans-serif">'
            f'{escape(primary)}</text>'
        )
        # Additional names on second line (comma-separated)
        if len(names) > 1:
            aliases = ", ".join(escape(nm) for nm in names[1:])
            parts.append(
                f'<text x="{_NODE_X + 12}" y="{y + 40}" '
                f'font-size="{_LABEL_FONT}" fill="#444" font-family="sans-serif">'
                f'also: {aliases}</text>'
            )
        # Type badge on right side
        if node_type:
            parts.append(
                f'<text x="{_NODE_X + _NODE_W - 10}" y="{y + 22}" '
                f'text-anchor="end" font-size="{_LABEL_FONT}" fill="#666" font-family="sans-serif">'
                f'[{escape(node_type)}]</text>'
            )
    parts.append("</svg>")
    return "\n".join(parts)


def render(snapshot: dict) -> str:
    """Render a snapshot dict into a self-contained HTML string.

    Pure: no network, no goldenmatch import. Deterministic: same snapshot -> identical output.
    """
    sc = snapshot["scaffolding"]
    llm = snapshot["recorded_llm"]

    protagonist = sc["protagonist"]
    entity_id = escape(str(protagonist["entity_id"]))
    question = escape(str(sc["question"]))

    before_nodes: list[dict] = sc["before"]["nodes"]
    after_nodes: list[dict] = sc["after"]["nodes"]

    before_answer = escape(str(llm["before_answer"]))
    after_answer = escape(str(llm["after_answer"]))

    exact_family_f1 = escape(str(sc["numbers"]["exact_family_f1"]))
    model = escape(str(llm["model"]))
    recorded_at = escape(str(llm["recorded_at"]))

    svg_before = _svg_nodes(before_nodes, title="Before: exact-match KG")
    svg_after = _svg_nodes(after_nodes, title="After: goldenmatch resolved")

    css = """
      * { box-sizing: border-box; margin: 0; padding: 0; }
      body {
        font-family: system-ui, sans-serif;
        background: #f5f7fa;
        color: #1a1a2e;
        padding: 24px 16px;
        line-height: 1.5;
      }
      h1 { font-size: 1.3rem; margin-bottom: 8px; }
      .question {
        background: #fff;
        border-left: 4px solid #4a80d4;
        padding: 10px 14px;
        margin-bottom: 20px;
        border-radius: 0 6px 6px 0;
        font-style: italic;
        font-size: 0.97rem;
      }
      .panels {
        display: flex;
        gap: 20px;
        flex-wrap: wrap;
        margin-bottom: 20px;
      }
      .panel {
        flex: 1 1 340px;
        background: #fff;
        border-radius: 8px;
        box-shadow: 0 1px 4px rgba(0,0,0,0.08);
        padding: 16px;
      }
      .panel h2 {
        font-size: 0.95rem;
        margin-bottom: 12px;
        color: #333;
        border-bottom: 1px solid #e0e0e0;
        padding-bottom: 6px;
      }
      .panel-before h2 { border-bottom-color: #e07a5f; }
      .panel-after h2  { border-bottom-color: #3d9970; }
      .panel svg { display: block; max-width: 100%; margin-bottom: 12px; }
      blockquote {
        background: #f0f4ff;
        border-left: 3px solid #4a80d4;
        padding: 8px 12px;
        border-radius: 0 4px 4px 0;
        font-size: 0.93rem;
      }
      .panel-before blockquote { background: #fff5f3; border-left-color: #e07a5f; }
      .panel-after blockquote  { background: #f0fff8; border-left-color: #3d9970; }
      .citation {
        font-size: 0.85rem;
        color: #555;
        margin-bottom: 20px;
        padding: 8px 12px;
        background: #fff;
        border-radius: 6px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.06);
      }
      footer {
        font-size: 0.8rem;
        color: #666;
        background: #fff;
        border-radius: 6px;
        padding: 12px 16px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.06);
        line-height: 1.7;
      }
      footer strong { color: #333; }
    """

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ER-KG-Bench demo: entity resolution flips the answer ({entity_id})</title>
<style>{css}</style>
</head>
<body>
<h1>ER-KG-Bench demo: entity resolution flips the answer ({entity_id})</h1>
<div class="question">{question}</div>
<div class="panels">
  <section class="panel panel-before">
    <h2>Before - exact-match KG (GraphRAG / mem0 family)</h2>
    {svg_before}
    <blockquote>{before_answer}</blockquote>
  </section>
  <section class="panel panel-after">
    <h2>After - goldenmatch zero-config (auto+fields)</h2>
    {svg_after}
    <blockquote>{after_answer}</blockquote>
  </section>
</div>
<div class="citation">
  The exact-match family scores <strong>{exact_family_f1}</strong> on real surface variation
  in ER-KG-Bench (Wikidata / RxNorm corpus, 206 records).
</div>
<footer>
  <strong>Honesty box:</strong>
  Corpus is real (Wikidata / RxNorm entities).
  The only construction is one ingested mention per surface form, which is exactly
  what real KG / agent-memory ingestion does.
  The "before" panel is the real exact-match family adapter, not a strawman.
  Agent answers are a real recorded run of <strong>{model}</strong> on <strong>{recorded_at}</strong>.
  Re-run with your own key via <code>demo/run_demo.py</code>.
</footer>
</body>
</html>"""
    return html
