# Offline corpus fixture

`offline_corpus.jsonl` is the **network-free, deterministic** corpus slice used by the
per-PR throughput perf gate (and the `offline` corpus adapter). It is paragraph-segmented
plain text from three **public-domain** Project Gutenberg books — no copyright, no license
restriction, no attribution required:

| Gutenberg ID | Title | Author |
|---|---|---|
| 1342 | Pride and Prejudice | Jane Austen |
| 11 | Alice's Adventures in Wonderland | Lewis Carroll |
| 84 | Frankenstein | Mary Shelley |

Each line is `{"doc_id": "pg<book>-<paragraph>", "text": "..."}`; paragraphs shorter than
200 chars are dropped. 1,552 docs, ~0.9 MB. Regenerate with the acquisition snippet in
`docs/superpowers/plans/2026-06-20-throughput-bench-ci-gate.md` (Task 1.1).

Why vendored: the gate must be deterministic and must never depend on network/HuggingFace
availability. Real-web-scale corpora (FineWeb/C4/Wikipedia) are streamed in-job for the
dispatch headline bench only — see `../corpora.py`.
