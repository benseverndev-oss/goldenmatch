# FS lever reachability

Two harnesses that answer one question about the Fellegi-Sunter path: not
"does autoconfig set every lever" (it should not), but **can the system reach
each lever, and does it know when to pull it?**

Every lever sits in one of four states:

| | has a trigger signal | no signal |
|---|---|---|
| **reachable** | healthy | blind lever (can pull, cannot aim) |
| **not reachable** | **detects and cannot act** | absent |

The bottom-left cell is the defect worth hunting: a working detector wired to
nothing.

## The harnesses

`lever_sweep.py` runs two sweeps over the same baseline config per dataset.
Sweep A moves the levers `build_probabilistic_matchkeys` hardcodes and never
revisits (`levels`, `partial_threshold`). Sweep B moves everything the closed
`ConfigEdit` vocabulary can actually reach. Comparing the two ceilings shows
whether the healer could ever get where lever-tuning gets.

`trigger_probe.py` asks whether the signals the engine already emits at
runtime predict WHICH lever to pull. A signal is a usable trigger only if its
firing tracks whether a cell is better or worse than the shipped default; one
that fires everywhere carries no information.

```bash
uv sync --package goldenmatch --extra polars   # the scripts import polars directly
python scripts/fs_levers/lever_sweep.py  --datasets ncvr_synthetic --out sweep.json
python scripts/fs_levers/trigger_probe.py --datasets ncvr_synthetic --out triggers.json
```

Datasets come from `scripts/suggest_quality/datasets.py` and skip cleanly when
absent. Only `ncvr_synthetic` and `historical_50k` carry information here; the
anchors saturate at F1 1.0 and measure nothing.

## What they found (2 datasets, 6 cells -- enough to locate gaps, not to tune)

**`link_threshold` is the clearest "detects and cannot act".** `auto_configure_
probabilistic_df` never sets it, so `mk.cutoff` is `None`,
`_perturbable_matchkeys` is empty, and `ThresholdShift` returns `None`.
Verified INERT on all four available datasets, so it is structural rather than
data-dependent. Meanwhile the engine emits a precise diagnosis at runtime
("linked N% ... using a FALLBACK link cutoff of 0.5000 ... Set 'link_threshold'").
The detector works, the actuator is disconnected, and the same emptiness leaves
`perturbation_stability` permanently unmeasured on the FS path.

**EM non-convergence is a second one.** "EM did not converge after 20
iterations" fired on exactly one cell of six -- the -0.34 F1 collapse -- and
nothing can raise `em_iterations` in response.

**Monotonicity is NOT a usable trigger**, which is the result that stops a
tempting change. It fires on 5 of 6 cells including both baselines, and
`n_monotonic_bad` runs the wrong way: ncvr's BEST cell has the most bad fields
(6), historical's worst has 3. Assuming the obvious direction would actively
mislead.

**`levels` is high-variance and dataset-dependent**: F1 0.48 to 0.98, with
`levels=5` the best value on ncvr (+0.0166) and catastrophic on historical
(-0.3406, and 6x slower). `levels=3` is the max-min choice, so the hardcoded
default is defensible. Adding a `LevelsEdit` without a trigger would hand the
healer a lever it cannot aim, with a -0.34 move inside its reach.

## Reading the numbers honestly

The sweep sets `levels` UNIFORMLY on every field, while autoconfig picks
per-field (`exact` fields get `levels=2, partial=0.9`). That is why a dataset's
baseline differs from its own `levels=3, partial=0.8` cell. The sweep measures
a strictly weaker configuration space than what ships; a real per-field edit
could beat both, and is unmeasured.

`trigger_probe.py` raises rather than returning zeros when it captures no
messages. Its first version wrapped the run in `warnings.catch_warnings`, but
the engine emits these through `logging`, so every `*_fired` came back `False`
while the warnings printed to the console. A capture that records nothing looks
exactly like a clean run.
