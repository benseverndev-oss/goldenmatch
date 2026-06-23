"""Unified metrics harness -- one command for the metrics that matter.

Runs a suite of offline, deterministic **probes** (accuracy: F1 / precision /
recall on labeled synthetic data; semantic blocking: candidate-generation recall
lift from the ANN source; trained embedder: alias-recall lift a trained in-house
model adds over the untrained projection; performance: wall, peak RSS, throughput,
stage timings) and writes a single structured report. Diffs the report against a committed
baseline (``baseline.json``) with per-metric tolerances so you can see, in one
command, whether a code change moved a metric.

Why this exists: the repo's metrics machinery is sophisticated but fragmented
(``run_benchmarks.py``, ``scale_audit*.py``, ``core/bench.py``, the FS panel) --
there was no single "run the numbers and tell me if I regressed" entry point and
no committed accuracy baseline. This is the offline core: synthetic accuracy +
perf that runs anywhere (no downloads, no Postgres, no API keys), so it's a
local-iteration tool today and a CI regression gate tomorrow. The download/key-
gated real datasets (DBLP-ACM, NCVR, DQbench) plug in as additional probes.

Usage::

    python scripts/metrics/harness.py                  # run + print summary
    python scripts/metrics/harness.py --out report.json --md summary.md
    python scripts/metrics/harness.py --check          # diff vs baseline (exit 1 on gated regression)
    python scripts/metrics/harness.py --update-baseline # re-snapshot the baseline from a fresh run

Determinism: the synthetic generator is seeded and the probes use an EXPLICIT
config (not nondeterministic zero-config auto-config), so accuracy metrics +
deterministic counts are stable run-to-run. Wall/RSS are machine-dependent and
recorded as informational (not gated).
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_BASELINE_PATH = _HERE / "baseline.json"
_SCHEMA = 1


# ── synthetic labeled data (deterministic, seeded, with ground truth) ─────────


def make_labeled(
    n_entities: int, *, seed: int = 7, dup_rate: float = 0.55
) -> tuple[list[dict[str, Any]], set[tuple[int, int]]]:
    """Build a labeled person dataset: each entity gets a clean row plus 0-2 messy
    duplicate variants. Returns ``(rows, ground_truth_pairs)`` where pairs are
    ``(min_pos, max_pos)`` over row POSITIONS (which become ``__row_id__``).

    Deterministic for a fixed ``seed`` -- so F1 and the row/pair counts are
    stable. The messiness (case / typo / whitespace, occasional email noise)
    exercises both the exact-email and the fuzzy-name matchkey.
    """
    import random

    rng = random.Random(seed)
    firsts = ["james", "mary", "john", "patricia", "robert", "jennifer", "michael",
              "linda", "william", "elizabeth", "david", "barbara", "richard", "susan",
              "joseph", "jessica", "thomas", "sarah", "charles", "karen", "daniel",
              "nancy", "matthew", "lisa", "anthony", "betty", "mark", "sandra",
              "donald", "ashley", "steven", "kimberly", "paul", "donna", "andrew",
              "carol", "joshua", "michelle", "kenneth", "amanda"]
    lasts = ["smith", "johnson", "williams", "brown", "jones", "garcia", "miller",
             "davis", "rodriguez", "martinez", "hernandez", "lopez", "wilson",
             "anderson", "thomas", "taylor", "moore", "jackson", "martin", "lee",
             "perez", "thompson", "white", "harris", "sanchez", "clark", "ramirez",
             "lewis", "robinson", "walker", "young", "allen", "king", "wright",
             "scott", "torres", "nguyen", "hill", "flores", "green"]
    cities = ["austin", "denver", "seattle", "boston", "miami", "chicago"]

    # Assign each entity a DISTINCT (first, last) so two different entities never
    # collide on name -- otherwise fuzzy-name would falsely merge same-name
    # strangers and tank precision. The combinatorial space (40x40=1600) must
    # exceed n_entities.
    combos = [(f, ln) for f in firsts for ln in lasts]
    if n_entities > len(combos):
        raise ValueError(f"make_labeled: n_entities {n_entities} exceeds {len(combos)} unique name combos")
    rng.shuffle(combos)
    names = combos[:n_entities]

    def _typo(s: str) -> str:
        if len(s) < 3:
            return s
        i = rng.randrange(1, len(s) - 1)
        return s[:i] + rng.choice("abcdefghijklmnopqrstuvwxyz") + s[i + 1:]

    def _mess(s: str) -> str:
        kind = rng.choice(["case", "typo", "ws", "case"])
        if kind == "case":
            return rng.choice([s.upper(), s.title(), s])
        if kind == "typo":
            return _typo(s)
        return rng.choice([" " + s, s + " ", "  " + s])

    rows: list[dict[str, Any]] = []
    entity_of: list[int] = []
    for e in range(n_entities):
        fn, ln = names[e]
        email = f"{fn}.{ln}{rng.randrange(1000)}@example.com"
        phone = f"{rng.randint(200, 999)}-{rng.randint(100, 999)}-{rng.randint(1000, 9999)}"
        zipc = f"{rng.randint(10000, 99999)}"
        city = rng.choice(cities)
        base = {"first_name": fn, "last_name": ln, "email": email,
                "phone": phone, "zip": zipc, "city": city}
        rows.append(dict(base)); entity_of.append(e)
        # duplicates
        n_dupes = rng.choices([0, 1, 2], weights=[1 - dup_rate, dup_rate * 0.7, dup_rate * 0.3])[0]
        for _ in range(n_dupes):
            d = dict(base)
            d["first_name"] = _mess(fn)
            d["last_name"] = _mess(ln)
            # ~25% of dupes have a noised email so exact-email alone can't catch them
            if rng.random() < 0.25:
                d["email"] = email.replace("@", " @ ")
            rows.append(d); entity_of.append(e)

    # shuffle so duplicates aren't adjacent (position == __row_id__ after shuffle)
    order = list(range(len(rows)))
    rng.shuffle(order)
    rows = [rows[i] for i in order]
    entity_of = [entity_of[i] for i in order]

    by_entity: dict[int, list[int]] = {}
    for pos, e in enumerate(entity_of):
        by_entity.setdefault(e, []).append(pos)
    gt: set[tuple[int, int]] = set()
    for members in by_entity.values():
        members.sort()
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                gt.add((members[i], members[j]))
    return rows, gt


def _dedupe(rows: list[dict[str, Any]]):
    """Run dedupe with an EXPLICIT, fixed config (deterministic; avoids zero-config
    auto-config nondeterminism). Exact-email OR fuzzy first+last name."""
    import polars as pl
    from goldenmatch import dedupe_df

    df = pl.DataFrame(rows)
    return dedupe_df(
        df,
        exact=["email"],
        fuzzy={"first_name": 0.88, "last_name": 0.88},
        confidence_required=False,
    )


def _recovered_pairs(result) -> set[tuple[int, int]]:
    """The within-cluster (min, max) position pairs a dedupe result merged.

    Cluster members are row positions (== ``__row_id__`` after the generator's
    shuffle), so this is directly comparable to the ground-truth pair set.
    """
    pred: set[tuple[int, int]] = set()
    for c in (result.clusters or {}).values():
        m = sorted(c["members"])
        for i in range(len(m)):
            for j in range(i + 1, len(m)):
                pred.add((m[i], m[j]))
    return pred


# ANN semantic-blocking operating point (offline zero-config in-house embedder).
# threshold 0.7 / top_k 20: the recall-leaning candidate-generation knob whose
# job is to REACH true pairs the structured/fuzzy keys miss. Scoring precision is
# a separate concern (the scorer's), so we measure candidate completeness, not F1.
_ANN_THRESHOLD = 0.7
_ANN_TOP_K = 20


def _ann_candidate_pairs(rows: list[dict[str, Any]]) -> set[tuple[int, int]]:
    """Candidate pairs the ANN semantic-blocking source emits over the full name,
    using the zero-config in-house embedder (deterministic, offline -- a fixed-seed
    random projection approximating char-n-gram overlap; no model file, env, cloud
    or torch). Gated at :data:`_ANN_THRESHOLD`."""
    import polars as pl
    from goldenmatch.config.schemas import BlockingConfig
    from goldenmatch.core.blocker import build_blocks

    df = (
        pl.DataFrame(rows)
        .with_row_index("__row_id__")
        .with_columns(
            (pl.col("first_name") + pl.lit(" ") + pl.col("last_name")).alias("__full_name__")
        )
    )
    blocks = build_blocks(
        df.lazy(),
        BlockingConfig(
            strategy="ann_pairs",
            ann_column="__full_name__",
            ann_model="inhouse",
            ann_top_k=_ANN_TOP_K,
        ),
    )
    cand: set[tuple[int, int]] = set()
    for blk in blocks:
        for a, b, s in (blk.pre_scored_pairs or []):
            if s >= _ANN_THRESHOLD:
                cand.add((min(a, b), max(a, b)))
    return cand


# ── probes ───────────────────────────────────────────────────────────────────


@dataclass
class ProbeOutcome:
    group: str
    metrics: dict[str, float]
    meta: dict[str, Any] = field(default_factory=dict)
    elapsed_s: float = 0.0
    error: str | None = None


def probe_accuracy() -> ProbeOutcome:
    """F1 / precision / recall on a labeled synthetic dedupe (fixed seed + config)."""
    from goldenmatch import evaluate_clusters

    rows, gt = make_labeled(n_entities=400, seed=7)
    res = _dedupe(rows)
    clusters = res.clusters or {}
    ev = evaluate_clusters(clusters, gt)
    return ProbeOutcome(
        group="accuracy",
        metrics={
            "f1": round(ev.f1, 4),
            "precision": round(ev.precision, 4),
            "recall": round(ev.recall, 4),
        },
        meta={"n_rows": len(rows), "gt_pairs": len(gt),
              "tp": ev.tp, "fp": ev.fp, "fn": ev.fn},
    )


def probe_perf(n_entities: int = 1500) -> ProbeOutcome:
    """Wall / peak RSS / throughput + stage timings on synthetic data of ~``n_entities``
    entities. Wall/RSS are machine-dependent (informational); counts are deterministic."""
    from goldenmatch.core.bench import bench_capture

    rows, _ = make_labeled(n_entities=n_entities, seed=11)
    t0 = time.perf_counter()
    with bench_capture() as rec:
        res = _dedupe(rows)
    wall = time.perf_counter() - t0
    peak_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0  # Linux: KB

    bench = rec.to_dict() if hasattr(rec, "to_dict") else {}
    bm = bench.get("metrics", {}) if isinstance(bench, dict) else {}
    clusters = res.clusters or {}
    multi = sum(1 for c in clusters.values() if c.get("size", 0) > 1)
    metrics = {
        "wall_s": round(wall, 4),
        "peak_rss_mb": round(peak_rss_mb, 1),
        "records_per_s": round(len(rows) / wall, 1) if wall else 0.0,
        "scored_pairs": int(bm.get("scored_pair_count", 0) or 0),
        "multi_member_clusters": multi,
    }
    return ProbeOutcome(
        group="perf",
        metrics=metrics,
        meta={"n_rows": len(rows), "stage_timings_s": bench.get("stage_timings_seconds", {})},
    )


def probe_semantic_blocking() -> ProbeOutcome:
    """Blocking-recall (pair completeness) lift from the ANN semantic-blocking source.

    Measures the thing semantic blocking (epic #1087) is actually responsible for:
    candidate-generation recall. On the same labeled set as ``accuracy_synthetic``,
    the structured exact-email + fuzzy-name keys plateau at a fixed recall -- the
    pairs they miss are missed at BLOCKING (verified: that recall is insensitive to
    the fuzzy threshold), not at scoring. The ANN source embeds the name with the
    zero-config in-house embedder and unions nearest-neighbor candidates, reaching
    pairs the lexical keys never co-locate.

    Reported as candidate completeness, NOT end-to-end F1: turning ANN candidates
    into merges is the scorer's job, and the untrained offline embedder over short
    names is deliberately not asked to also carry precision (a trained embedder
    would shrink ``ann_candidate_pairs`` for the same recall -- which this probe
    would then show). Fully offline + deterministic.
    """
    rows, gt = make_labeled(n_entities=400, seed=7)
    base = _recovered_pairs(_dedupe(rows))
    missed = gt - base
    cand = _ann_candidate_pairs(rows)
    recovered = missed & cand

    n_gt = len(gt) or 1
    base_recall = len(gt & base) / n_gt
    sem_recall = (len(gt & base) + len(recovered)) / n_gt
    return ProbeOutcome(
        group="accuracy",
        metrics={
            "blocking_recall_baseline": round(base_recall, 4),
            "blocking_recall_semantic": round(sem_recall, 4),
            "recall_lift": round(sem_recall - base_recall, 4),
            "ann_pairs_recovered": len(recovered),
            "ann_candidate_pairs": len(cand),
        },
        meta={"gt_pairs": len(gt), "missed_by_structured": len(missed),
              "ann_threshold": _ANN_THRESHOLD, "ann_top_k": _ANN_TOP_K},
    )


# ── trained-embedder probe ────────────────────────────────────────────────────
# Nickname aliases with near-ZERO character overlap (robert<->bob). A lexical /
# char-n-gram embedder structurally cannot see the equivalence; a TRAINED embedder
# learns it from labeled pairs. This is the signal that isolates a trained model's
# value -- surface-noise typos (the semantic probe's data) are already near-ceiling
# for the untrained char-n-gram random projection, so training is ~a no-op there.
_ALIASES = {"robert": "bob", "william": "bill", "elizabeth": "liz", "richard": "dick",
            "margaret": "peg", "james": "jim", "john": "jack", "charles": "chuck",
            "michael": "mike"}
# DISJOINT surname vocabularies: the model trains on _TRAIN_SURNAMES and is
# evaluated on _EVAL_SURNAMES, so a recall lift is generalization (the alias
# equivalence transferring to unseen full names), not memorization.
_TRAIN_SURNAMES = ("baker", "fox", "webb", "rose", "pike", "lamb", "frost", "nash")
_EVAL_SURNAMES = ("smith", "jones", "brown", "lee", "clark", "king", "hill", "green",
                  "ford", "gray", "ward", "cole", "reed", "shaw", "wood", "bell",
                  "hunt", "dean", "page", "watt")


def _make_alias_labeled(n_entities: int, seed: int) -> tuple[list[dict[str, Any]], set[tuple[int, int]]]:
    """Each entity = a formal-name row + an alias-name duplicate (robert/bob), over
    ``_EVAL_SURNAMES``. Returns ``(rows, alias_pairs)`` where ``alias_pairs`` is the
    HARD subset: same person, first names with near-zero character overlap."""
    import random

    rng = random.Random(seed)
    combos = [(f, ln) for f in _ALIASES for ln in _EVAL_SURNAMES]
    if n_entities > len(combos):
        raise ValueError(f"_make_alias_labeled: n_entities {n_entities} exceeds {len(combos)} combos")
    rng.shuffle(combos)
    rows: list[dict[str, Any]] = []
    alias_pairs: set[tuple[int, int]] = set()
    for first, last in combos[:n_entities]:
        p0 = len(rows)
        rows.append({"first_name": first, "last_name": last})
        rows.append({"first_name": _ALIASES[first], "last_name": last})
        alias_pairs.add((p0, p0 + 1))
    return rows, alias_pairs


def _train_alias_embedder(model_path: Path) -> float:
    """Train (offline, seeded, numpy-only) a GoldenEmbedModel on alias pairs built
    over ``_TRAIN_SURNAMES``, save it to ``model_path``, return the train separation
    (mean match cosine - mean non-match cosine; higher = better learned)."""
    import random

    from goldenmatch.embeddings.inhouse.trainer import TrainConfig, train_embedder

    rng = random.Random(0)
    pos = [(f"{formal} {s}", f"{nick} {s}", 1)
           for formal, nick in _ALIASES.items() for s in _TRAIN_SURNAMES]
    firsts = list(_ALIASES)
    neg: list[tuple[str, str, int]] = []
    while len(neg) < len(pos):
        a, b = rng.choice(firsts), rng.choice(firsts)
        s1, s2 = rng.choice(_TRAIN_SURNAMES), rng.choice(_TRAIN_SURNAMES)
        if a != b:
            neg.append((f"{a} {s1}", f"{b} {s2}", 0))
    model, report = train_embedder(pos + neg, TrainConfig(dim=64, epochs=200, seed=0))
    model.save(model_path)
    return report.separation_after


def _alias_ann_candidates(rows: list[dict[str, Any]], model_id: str) -> set[tuple[int, int]]:
    """ANN candidate pairs over full names, embedded by ``model_id`` (``"inhouse"``
    for the untrained projection, ``"inhouse:<path>"`` for a trained model)."""
    import polars as pl
    from goldenmatch.config.schemas import BlockingConfig
    from goldenmatch.core.blocker import build_blocks

    # Distinct column name from the semantic probe's "__full_name__": the embedder
    # caches embeddings by cache_key=f"ann_{ann_column}", so a shared name collides
    # across probes (the alias 360-row data would reuse the semantic probe's cached
    # 1000+-row embeddings -> pair indices overflow row_ids).
    df = (
        pl.DataFrame(rows)
        .with_row_index("__row_id__")
        .with_columns(
            (pl.col("first_name") + pl.lit(" ") + pl.col("last_name")).alias("__alias_full_name__")
        )
    )
    blocks = build_blocks(
        df.lazy(),
        BlockingConfig(strategy="ann_pairs", ann_column="__alias_full_name__",
                       ann_model=model_id, ann_top_k=_ANN_TOP_K),
    )
    cand: set[tuple[int, int]] = set()
    for blk in blocks:
        for a, b, s in (blk.pre_scored_pairs or []):
            if s >= _ANN_THRESHOLD:
                cand.add((min(a, b), max(a, b)))
    return cand


def probe_trained_embedder() -> ProbeOutcome:
    """Recall lift a TRAINED in-house embedder adds over the untrained projection.

    The semantic probe showed the untrained char-n-gram projection already reaches
    typo'd-name pairs -- but it is structurally blind to equivalences with no
    surface overlap (nickname aliases: robert<->bob). This probe isolates exactly
    that: alias-pair blocking recall with the untrained embedder vs a model trained
    (offline, seeded, numpy-only -- no torch/cloud) on alias pairs over a DISJOINT
    surname vocabulary. The lift is the trained model's value as a tracked number:
    "we trained a better embedder" becomes a measurement, not a claim.

    Untrained recall sits near the floor (char-n-grams can't bridge robert->bob);
    trained recall approaches 1.0 on unseen surnames (the equivalence generalizes).
    Fully offline + deterministic.
    """
    import tempfile

    rows, alias_pairs = _make_alias_labeled(n_entities=180, seed=7)
    n_alias = len(alias_pairs) or 1
    untrained = _alias_ann_candidates(rows, "inhouse")
    untrained_rec = alias_pairs & untrained
    with tempfile.TemporaryDirectory() as tmp:
        model_path = Path(tmp) / "alias_model"
        separation = _train_alias_embedder(model_path)
        trained = _alias_ann_candidates(rows, f"inhouse:{model_path}")
    trained_rec = alias_pairs & trained

    r_untrained = len(untrained_rec) / n_alias
    r_trained = len(trained_rec) / n_alias
    return ProbeOutcome(
        group="accuracy",
        metrics={
            "alias_recall_untrained": round(r_untrained, 4),
            "alias_recall_trained": round(r_trained, 4),
            "recall_gain": round(r_trained - r_untrained, 4),
            "train_separation": round(separation, 4),
            "untrained_candidates": len(untrained),
            "trained_candidates": len(trained),
        },
        meta={"alias_pairs": len(alias_pairs), "ann_threshold": _ANN_THRESHOLD,
              "ann_top_k": _ANN_TOP_K, "train_surnames": len(_TRAIN_SURNAMES),
              "eval_surnames": len(_EVAL_SURNAMES)},
    )


PROBES: dict[str, Callable[[], ProbeOutcome]] = {
    "accuracy_synthetic": probe_accuracy,
    "accuracy_synthetic_semantic": probe_semantic_blocking,
    "accuracy_trained_embedder": probe_trained_embedder,
    "perf_synthetic": probe_perf,
}


# ── report assembly ───────────────────────────────────────────────────────────


def _git_info() -> dict[str, Any]:
    def _run(args: list[str]) -> str:
        try:
            return subprocess.check_output(
                ["git", *args], cwd=_HERE, stderr=subprocess.DEVNULL, text=True
            ).strip()
        except Exception:
            return ""
    return {
        "sha": _run(["rev-parse", "HEAD"]),
        "branch": _run(["rev-parse", "--abbrev-ref", "HEAD"]),
        "dirty": bool(_run(["status", "--porcelain"])),
    }


def _env_info() -> dict[str, Any]:
    native = False
    try:
        from goldenmatch.core._native_loader import native_available
        native = native_available()
    except Exception:
        pass
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "native_kernel": native,
    }


def _warmup() -> None:
    """Run one tiny dedupe so import / first-call costs don't skew the first
    probe's wall time (the engine, rapidfuzz, polars all init lazily)."""
    try:
        rows, _ = make_labeled(n_entities=20, seed=1)
        _dedupe(rows)
    except Exception:
        pass


def run_report(selected: list[str] | None = None) -> dict[str, Any]:
    names = selected or list(PROBES)
    _warmup()
    probes_out: dict[str, Any] = {}
    for name in names:
        fn = PROBES.get(name)
        if fn is None:
            probes_out[name] = {"error": f"unknown probe {name!r}"}
            continue
        t0 = time.perf_counter()
        try:
            outcome = fn()
            outcome.elapsed_s = round(time.perf_counter() - t0, 3)
            probes_out[name] = {
                "group": outcome.group, "metrics": outcome.metrics,
                "meta": outcome.meta, "elapsed_s": outcome.elapsed_s,
            }
        except Exception as exc:  # one probe failing must not kill the report
            probes_out[name] = {"error": f"{type(exc).__name__}: {exc}",
                                "elapsed_s": round(time.perf_counter() - t0, 3)}
    return {
        "schema": _SCHEMA,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git": _git_info(),
        "env": _env_info(),
        "probes": probes_out,
    }


def _flatten(report: dict[str, Any]) -> dict[str, float]:
    """``probe.metric`` -> value for every numeric metric in the report."""
    out: dict[str, float] = {}
    for pname, p in report.get("probes", {}).items():
        for mname, val in (p.get("metrics") or {}).items():
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                out[f"{pname}.{mname}"] = float(val)
    return out


# ── baseline + diff ───────────────────────────────────────────────────────────

# Which metrics are GATED (a regression past tolerance is a real failure) vs
# informational. Wall/RSS/throughput are machine-dependent -> informational.
# Accuracy + deterministic counts are gated.
_GATED_SUFFIXES = ("f1", "precision", "recall", "scored_pairs", "multi_member_clusters",
                   "blocking_recall_baseline", "blocking_recall_semantic",
                   "ann_pairs_recovered", "ann_candidate_pairs",
                   "alias_recall_untrained", "alias_recall_trained",
                   "untrained_candidates", "trained_candidates")
_INFO_SUFFIXES = ("wall_s", "peak_rss_mb", "records_per_s", "recall_lift",
                  "recall_gain", "train_separation")
# direction: True = higher is better (regression = drop); False = lower is better.
_HIGHER_BETTER = ("f1", "precision", "recall", "records_per_s",
                  "blocking_recall_baseline", "blocking_recall_semantic",
                  "alias_recall_untrained", "alias_recall_trained")
# Deterministic counts have no "better" direction -- they're env-independent
# fingerprints of the pipeline, so ANY drift (up OR down) past tolerance is a
# regression. A drop here is exactly the signal we want (e.g. blocking losing
# candidate pairs); the one-sided higher/lower gate would miss it.
_TWO_SIDED = ("scored_pairs", "multi_member_clusters",
              "ann_pairs_recovered", "ann_candidate_pairs",
              "untrained_candidates", "trained_candidates")
# default per-metric tolerance bands.
_TOL = {"f1": 0.02, "precision": 0.02, "recall": 0.02,
        "scored_pairs": 0.0, "multi_member_clusters": 0.0,
        "blocking_recall_baseline": 0.02, "blocking_recall_semantic": 0.02,
        "ann_pairs_recovered": 0.0, "ann_candidate_pairs": 0.0,
        "alias_recall_untrained": 0.02, "alias_recall_trained": 0.02,
        "untrained_candidates": 0.0, "trained_candidates": 0.0,
        "wall_s": 9e9, "peak_rss_mb": 9e9, "records_per_s": 9e9,
        "recall_lift": 9e9, "recall_gain": 9e9, "train_separation": 9e9}


def _suffix(key: str) -> str:
    return key.rsplit(".", 1)[-1]


def build_baseline(report: dict[str, Any]) -> dict[str, Any]:
    metrics = {}
    for key, val in _flatten(report).items():
        suf = _suffix(key)
        if suf not in _GATED_SUFFIXES and suf not in _INFO_SUFFIXES:
            continue
        metrics[key] = {
            "value": val,
            "tol": _TOL.get(suf, 0.0),
            "higher_better": suf in _HIGHER_BETTER,
            "two_sided": suf in _TWO_SIDED,
            "gated": suf in _GATED_SUFFIXES,
        }
    return {"schema": _SCHEMA,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "metrics": metrics}


def load_baseline() -> dict[str, Any] | None:
    if not _BASELINE_PATH.is_file():
        return None
    return json.loads(_BASELINE_PATH.read_text())


def diff_against_baseline(report: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    """Return ``{rows: [...], regressions: [...]}``. A regression is a GATED metric
    that moved past its tolerance in the wrong direction."""
    cur = _flatten(report)
    rows = []
    regressions = []
    for key, spec in baseline.get("metrics", {}).items():
        base = float(spec["value"])
        now = cur.get(key)
        if now is None:
            rows.append({"metric": key, "baseline": base, "current": None,
                         "delta": None, "status": "MISSING"})
            continue
        delta = now - base
        tol = float(spec.get("tol", 0.0))
        higher_better = bool(spec.get("higher_better", True))
        two_sided = bool(spec.get("two_sided", False))
        gated = bool(spec.get("gated", False))
        # regressed if moved in the wrong direction past tolerance. Two-sided
        # (deterministic-count) metrics regress on ANY drift past tolerance.
        if two_sided:
            worse = abs(delta) > tol
        else:
            worse = (delta < -tol) if higher_better else (delta > tol)
        status = "ok"
        if worse and gated:
            status = "REGRESSION"
            regressions.append({"metric": key, "baseline": base, "current": now, "delta": round(delta, 4)})
        elif worse:
            status = "moved"
        rows.append({"metric": key, "baseline": base, "current": now,
                     "delta": round(delta, 4), "gated": gated, "status": status})
    return {"rows": rows, "regressions": regressions}


# ── rendering ──────────────────────────────────────────────────────────────────


def to_markdown(report: dict[str, Any], diff: dict[str, Any] | None = None) -> str:
    g = report.get("git", {})
    e = report.get("env", {})
    lines = [
        "## GoldenMatch metrics",
        "",
        f"- commit `{(g.get('sha') or '')[:12]}`"
        f"{' (dirty)' if g.get('dirty') else ''} on `{g.get('branch','')}`",
        f"- python {e.get('python','?')} · {e.get('cpu_count','?')} cpu · "
        f"native kernel: {'yes' if e.get('native_kernel') else 'no'}",
        "",
    ]
    for pname, p in report.get("probes", {}).items():
        if p.get("error"):
            lines.append(f"### {pname} — ERROR: {p['error']}")
            continue
        lines.append(f"### {pname} ({p.get('group','')}, {p.get('elapsed_s','?')}s)")
        lines.append("")
        lines.append("| metric | value |")
        lines.append("|---|---|")
        for k, v in p.get("metrics", {}).items():
            lines.append(f"| {k} | {v} |")
        lines.append("")
    if diff is not None:
        lines.append("### vs baseline")
        lines.append("")
        lines.append("| metric | baseline | current | delta | status |")
        lines.append("|---|---|---|---|---|")
        for r in diff.get("rows", []):
            lines.append(
                f"| {r['metric']} | {r['baseline']} | {r['current']} | "
                f"{r['delta']} | {r['status']} |"
            )
        lines.append("")
        regs = diff.get("regressions", [])
        lines.append(f"**{len(regs)} gated regression(s).**" if regs else "**No gated regressions.**")
    return "\n".join(lines) + "\n"


# ── CLI ────────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="GoldenMatch unified metrics harness")
    ap.add_argument("--probes", default="", help="comma-separated probe names (default: all)")
    ap.add_argument("--out", default="", help="write the JSON report here")
    ap.add_argument("--md", default="", help="write a markdown summary here (or '-' for stdout)")
    ap.add_argument("--check", action="store_true", help="diff vs baseline; exit 1 on a gated regression")
    ap.add_argument("--update-baseline", action="store_true", help="re-snapshot baseline.json from this run")
    args = ap.parse_args(argv)

    selected = [s.strip() for s in args.probes.split(",") if s.strip()] or None
    report = run_report(selected)

    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2))

    if args.update_baseline:
        _BASELINE_PATH.write_text(json.dumps(build_baseline(report), indent=2))
        print(f"Updated baseline: {_BASELINE_PATH}")
        return 0

    diff = None
    baseline = load_baseline()
    if (args.check or args.md) and baseline is not None:
        diff = diff_against_baseline(report, baseline)

    md = to_markdown(report, diff)
    if args.md == "-" or not args.md:
        print(md)
    elif args.md:
        Path(args.md).write_text(md)

    if args.check:
        if baseline is None:
            print("No baseline to check against (run --update-baseline first).", file=sys.stderr)
            return 0
        regs = diff.get("regressions", []) if diff else []
        if regs:
            print(f"::error::{len(regs)} gated metric regression(s): "
                  + ", ".join(r["metric"] for r in regs), file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
