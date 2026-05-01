# TUI

Built with [Textual](https://textual.textualize.io/). Entry point: `GoldenCheckApp` in `app.py`.

## 4 Tabs

| Key | Tab ID | Pane class | Content |
|---|---|---|---|
| `1` | `overview` | `OverviewPane` | DQBench score, severity summary, column health grid |
| `2` | `findings` | `FindingsPane` | Sortable findings table; Space to pin, `e` to view affected rows |
| `3` | `column-detail` | `ColumnDetailPane` | Per-column stats (null%, unique%, type) |
| `4` | `rules` | `RulesPane` | Pinned rules preview — what will be written to `goldencheck.yml` |

## Keybindings

| Key | Action |
|---|---|
| `1`–`4` | Switch tabs |
| `Space` | Pin/unpin selected finding (FindingsPane) |
| `F2` | Save pinned findings to `goldencheck.yml` |
| `e` | View rows affected by selected finding |
| `q` | Quit |
| `?` | Show help notification |

## Gold Theme CSS (inline in `app.py`)

```css
.gold { color: #FFD700; }
.health-a { color: #00ff00; }   /* A ≥ 90 */
.health-b { color: #7fff00; }   /* B 80-89 */
.health-c { color: #ffff00; }   /* C 70-79 */
.health-d { color: #ff7f00; }   /* D 60-69 */
.health-f { color: #ff0000; }   /* F < 60 */
.severity-error { color: red; }
.severity-warning { color: yellow; }
.severity-info { color: cyan; }
```

To add a new CSS class, add it to the `CSS` string on `GoldenCheckApp` — no external stylesheet.

## Launching the TUI

```python
from goldencheck.tui.app import GoldenCheckApp
app = GoldenCheckApp(findings=findings, profile=profile, config=cfg)  # config optional
app.run()
```

## Saving Rules (F2 / action_save_rules)

Pinned findings (`finding.pinned == True`) get added to `config.columns` as `ColumnRule(type="string")`. Config is written to `goldencheck.yml` via `goldencheck.config.writer.save_config`.

## FindingsPane Columns

`["", "Severity", "Column", "Check", "Message", "Rows", "Conf", "Source"]`

- **Conf**: H (>=0.8), M (0.5-0.79), L (<0.5) — confidence score indicator
- **Source**: `[LLM]` if finding came from LLM boost, empty otherwise

## Gotchas

- `GoldenCheckApp.__init__` accepts an optional `config: GoldenCheckConfig` — pass it when launching from `validate`/`review` so existing rules are visible in the Rules tab
- Tab IDs must match the `id=` in `TabPane(...)` and the strings passed to `action_switch_tab()` — they are `"overview"`, `"findings"`, `"column-detail"`, `"rules"` (note the hyphen in column-detail)
- Textual requires Python ≥ 3.11 and `textual>=1.0` — same constraint as the project
- `fix`, `diff`, and `watch` commands are CLI-only — they don't launch the TUI
- `Finding.metadata` dict exists but is not displayed in the TUI (available for future columns)
