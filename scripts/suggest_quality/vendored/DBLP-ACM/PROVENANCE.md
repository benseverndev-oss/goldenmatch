# DBLP-ACM — vendored benchmark data

Third-party data, committed deliberately. Read this before moving, regenerating,
or deleting it.

## Why it is committed rather than downloaded

`scripts/suggest_quality` gates on a committed scorecard. A dataset that is
fetched at run time is *absent* whenever the upstream host is unreachable, and
the gate cannot distinguish "upstream is down" from "a blessed dataset silently
stopped running" — the check-exists-but-does-not-fire failure this repo keeps
paying for. Blessing a dataset the gate cannot guarantee it can load converts a
legitimate skip into a permanent `MISSING` failure (that is exactly what
happened in #2566, and it is why #2635 exists).

At 852 KB uncompressed the whole corpus is smaller than the `historical_50k`
fixture already committed under `scripts/autoconfig_quality/vendored/`, so
vendoring costs essentially nothing and makes the gate deterministic.

**`tests/benchmarks/datasets/` is deliberately NOT the home for this.** That
tree is gitignored on purpose ("benchmark datasets downloaded at runtime — not
committed", root `.gitignore`) and the loader still falls back to it, so the
runtime-download path for NCVR and friends is unchanged. `vendored/` means
committed; `datasets/` means fetched. Keep the two distinct rather than
carving holes in the ignore rules.

## Source and attribution

Published by the database group of Prof. Erhard Rahm, Universität Leipzig, as
part of their entity-resolution benchmark datasets.

- Website: https://dbs.uni-leipzig.de/research/projects/benchmark-datasets-for-entity-resolution
- Archive: https://dbs.uni-leipzig.de/file/DBLP-ACM.zip
- Retrieved: 2026-08-17

The datasets are released under a Creative Commons licence. The publishers ask
that credit be given by referring to their website **and** citing the VLDB 2010
paper:

> Hanna Köpcke, Andreas Thor, Erhard Rahm.
> *Evaluation of entity resolution approaches on real-world match problems.*
> Proceedings of the VLDB Endowment, 3(1–2), 2010.

Attribution is a licence condition, not a courtesy — keep this file alongside
the data if the data moves.

**Licence-version caveat:** the source page states "the Creative Commons
license" without naming a variant, so the precise version is unspecified
upstream. Redistribution with attribution is clearly permitted; do not assert a
specific CC variant (e.g. "CC BY 4.0") in downstream docs without confirming it.

## Contents and integrity

| file | sha256 |
|---|---|
| `DBLP2.csv` | `a74ced040108a9aea20345a8e21f763a03e284abc22b4b99d64b44e47a99485c` |
| `ACM.csv` | `32055f1dfa619a4fdca33e7de729c66686a2fb3c71589921a6a3bd3af389120e` |
| `DBLP-ACM_perfectMapping.csv` | `d9d7c9feaba3d19a2e73ba8bd6ae08407d8b16082881f6e55abc2d703682d53a` |

2,616 DBLP records + 2,294 ACM records, 2,224 ground-truth matching pairs. Every
true pair is cross-source; neither source contains internal duplicates (see
#2634 — that property is why this corpus is *not* evidence for a within-source
constraint in `dedupe_df`).

**Encoding:** both `DBLP2.csv` and `ACM.csv` are latin-1, not UTF-8. Read them
with `encoding="utf8-lossy"` — a plain UTF-8 read raises.
