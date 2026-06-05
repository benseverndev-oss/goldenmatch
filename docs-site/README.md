# Golden Suite docs

Mintlify documentation site for the Golden Suite monorepo.

## Preview locally

```bash
npm i -g mint        # one-time
cd docs-site
mint dev             # http://localhost:3000
```

## Validate before pushing

```bash
mint validate        # strict build check (fails on warnings)
mint broken-links    # check internal links and anchors
mint a11y            # alt-text and contrast checks
```

## Structure

- `docs.json` — site config and navigation.
- `index.mdx`, `quickstart.mdx` — landing and 30-second quickstart.
- `concepts/` — architecture, entity resolution, scale envelope.
- `goldenmatch/`, `goldencheck/`, `goldenflow/`, `goldenpipe/`, `infermap/` — per-package docs.
- `extensions/` — Postgres and DuckDB SQL extensions.
- `reference/` — vendor comparison and other reference material.

Content is sourced from the package `README.md` files. When a package's public API
or CLI changes, update both the README and the matching page here.
