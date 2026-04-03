# Deep Profiling & Baseline Drift Detection — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `goldencheck baseline` command that runs 6 statistical analysis techniques on a dataset, saves a YAML profile, and enables fast drift detection on future scans.

**Architecture:** New `baseline/` and `drift/` subpackages under `goldencheck/`. Baseline creation runs 6 techniques in order (semantic → statistical → constraints → correlation → patterns → priors). Drift detection integrates into `scan_file()` via an optional `baseline` parameter. scipy/numpy isolated to these subpackages only.

**Tech Stack:** Python 3.12, Polars, scipy, numpy (optional deps), sentence-transformers (optional), Pydantic 2, PyYAML, Typer

**Spec:** `docs/superpowers/specs/2026-04-03-deep-profiling-baseline-design.md`

---

## File Map

### New Files

| File | Responsibility |
|---|---|
| `goldencheck/baseline/__init__.py` | Public API: `create_baseline()`, `load_baseline()` |
| `goldencheck/baseline/models.py` | Pydantic models: `BaselineProfile`, `StatProfile`, `FunctionalDependency`, `TemporalOrder`, `CorrelationEntry`, `PatternGrammar`, `ConfidencePrior` |
| `goldencheck/baseline/statistical.py` | Distribution fitting, Benford's Law, entropy, percentile bounds |
| `goldencheck/baseline/constraints.py` | TANE FD mining + temporal order discovery |
| `goldencheck/baseline/semantic.py` | Embedding-based type inference (optional sentence-transformers) |
| `goldencheck/baseline/correlation.py` | Mutual info, Cramer's V, Pearson/Spearman |
| `goldencheck/baseline/patterns.py` | Character-class grammar induction |
| `goldencheck/baseline/priors.py` | Confidence prior builder from baseline findings |
| `goldencheck/drift/__init__.py` | Public API: `run_drift_checks()` |
| `goldencheck/drift/detector.py` | 6 drift detector classes, one per technique |
| `tests/baseline/__init__.py` | (empty) |
| `tests/baseline/test_models.py` | Baseline model serialization round-trips |
| `tests/baseline/test_statistical.py` | Distribution fitting, Benford's, entropy |
| `tests/baseline/test_constraints.py` | FD mining, temporal orders |
| `tests/baseline/test_semantic.py` | Embedding inference + fallback |
| `tests/baseline/test_correlation.py` | Correlation analysis |
| `tests/baseline/test_patterns.py` | Grammar induction |
| `tests/baseline/test_priors.py` | Prior builder |
| `tests/baseline/test_create_baseline.py` | End-to-end baseline creation |
| `tests/drift/__init__.py` | (empty) |
| `tests/drift/test_detector.py` | Drift detection against known baselines |
| `tests/drift/test_integration.py` | scan_file() with baseline parameter |

### Modified Files

| File | Changes |
|---|---|
| `goldencheck/__init__.py` | Add lazy exports: `create_baseline`, `load_baseline` |
| `goldencheck/engine/scanner.py` | Add `baseline` param to `scan_file()`, integrate drift checks |
| `goldencheck/cli/main.py` | Add `baseline` command, `--baseline`/`--no-baseline` flags on scan, update hand-rolled parser |
| `goldencheck/models/finding.py` | Update `_repr_html_()` for `source="baseline_drift"` |
| `pyproject.toml` | Add `[baseline]` and `[semantic]` optional dependency groups |

---

## Task 1: Pydantic Baseline Models

**Files:**
- Create: `goldencheck/baseline/__init__.py`
- Create: `goldencheck/baseline/models.py`
- Create: `tests/baseline/__init__.py`
- Create: `tests/baseline/test_models.py`

- [ ] **Step 1: Write model tests**

```python
# tests/baseline/test_models.py
"""Tests for baseline Pydantic models and YAML serialization."""
from __future__ import annotations

import yaml
from pathlib import Path
from goldencheck.baseline.models import (
    BaselineProfile,
    StatProfile,
    FunctionalDependency,
    TemporalOrder,
    CorrelationEntry,
    PatternGrammar,
    ConfidencePrior,
)


class TestStatProfile:
    def test_create_with_all_fields(self):
        p = StatProfile(
            distribution="log_normal",
            params={"mu": 10.2, "sigma": 0.8},
            benford={"passes": False, "chi2": 45.2},
            entropy=4.82,
            bounds={"p01": 12000, "p99": 250000},
        )
        assert p.distribution == "log_normal"
        assert p.entropy == 4.82

    def test_create_minimal(self):
        p = StatProfile(entropy=3.5, bounds={"p01": 0, "p99": 100})
        assert p.distribution is None
        assert p.benford is None


class TestFunctionalDependency:
    def test_create(self):
        fd = FunctionalDependency(
            determinant=["zip_code"],
            dependent=["city", "state"],
            confidence=0.97,
        )
        assert fd.determinant == ["zip_code"]
        assert fd.confidence == 0.97


class TestBaselineProfile:
    def test_round_trip_yaml(self, tmp_path: Path):
        profile = BaselineProfile(
            version="1.0",
            source="test.csv",
            rows=1000,
            columns=5,
            statistical_profiles={
                "age": StatProfile(
                    distribution="normal",
                    params={"mu": 42.0, "sigma": 15.0},
                    entropy=3.9,
                    bounds={"p01": 18, "p99": 95},
                ),
            },
            constraints_fd=[
                FunctionalDependency(
                    determinant=["zip"], dependent=["city"], confidence=0.95,
                ),
            ],
            constraints_keys=[{"columns": ["id"], "unique": True}],
            constraints_temporal=[
                TemporalOrder(before="start", after="end", violation_rate=0.002),
            ],
            semantic_types={"email": ["email", "backup_email"]},
            correlations=[
                CorrelationEntry(
                    columns=["city", "state"],
                    measure="cramers_v",
                    value=0.94,
                    strength="strong",
                ),
            ],
            patterns={
                "code": [PatternGrammar(pattern="[A-Z]{3}-[0-9]{4}", coverage=0.98)],
            },
            confidence_priors={
                "format_detection": {
                    "email": ConfidencePrior(confidence=0.95, evidence_count=4800),
                },
            },
        )
        out = tmp_path / "baseline.yaml"
        profile.save(out)
        loaded = BaselineProfile.load(out)
        assert loaded.rows == 1000
        assert loaded.statistical_profiles["age"].distribution == "normal"
        assert loaded.constraints_fd[0].confidence == 0.95
        assert loaded.semantic_types["email"] == ["email", "backup_email"]
        assert loaded.correlations[0].value == 0.94
        assert loaded.patterns["code"][0].coverage == 0.98

    def test_load_ignores_unknown_keys(self, tmp_path: Path):
        data = {
            "version": "2.0",
            "source": "test.csv",
            "rows": 100,
            "columns": 2,
            "future_field": "should be ignored",
        }
        out = tmp_path / "baseline.yaml"
        out.write_text(yaml.dump(data))
        profile = BaselineProfile.load(out)
        assert profile.version == "2.0"
        assert profile.rows == 100

    def test_source_filename(self):
        profile = BaselineProfile(
            version="1.0", source="data/input.csv", rows=100, columns=5,
        )
        assert profile.source_filename == "input.csv"
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `cd D:/show_case/goldencheck && python -m pytest tests/baseline/test_models.py -v`
Expected: ImportError — `goldencheck.baseline.models` does not exist

- [ ] **Step 3: Create empty `__init__.py` files**

```python
# goldencheck/baseline/__init__.py
"""Deep profiling baseline — learn-once, monitor-forever."""
```

```python
# tests/baseline/__init__.py
```

- [ ] **Step 4: Implement models**

```python
# goldencheck/baseline/models.py
"""Pydantic models for baseline profiles."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class StatProfile(BaseModel):
    """Statistical profile for a single numeric column."""

    distribution: str | None = None
    params: dict[str, float] | None = None
    benford: dict[str, float | bool] | None = None
    entropy: float
    bounds: dict[str, float]


class FunctionalDependency(BaseModel):
    """A discovered functional dependency."""

    determinant: list[str]
    dependent: list[str]
    confidence: float


class TemporalOrder(BaseModel):
    """A discovered temporal ordering constraint."""

    before: str
    after: str
    violation_rate: float


class CorrelationEntry(BaseModel):
    """A discovered column-pair correlation."""

    columns: list[str]
    measure: str  # "cramers_v", "mutual_info", "pearson", "spearman"
    value: float
    strength: str  # "strong", "moderate", "weak"
    note: str | None = None


class PatternGrammar(BaseModel):
    """A learned value grammar for a column."""

    pattern: str
    coverage: float


class ConfidencePrior(BaseModel):
    """A calibration prior for a check type + column."""

    confidence: float
    evidence_count: int


class BaselineProfile(BaseModel):
    """Complete baseline profile for a dataset."""

    version: str = "1.0"
    created: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source: str = ""
    rows: int = 0
    columns: int = 0

    statistical_profiles: dict[str, StatProfile] = Field(default_factory=dict)
    constraints_fd: list[FunctionalDependency] = Field(default_factory=list)
    constraints_keys: list[dict] = Field(default_factory=list)
    constraints_temporal: list[TemporalOrder] = Field(default_factory=list)
    semantic_types: dict[str, list[str]] = Field(default_factory=dict)
    correlations: list[CorrelationEntry] = Field(default_factory=list)
    patterns: dict[str, list[PatternGrammar]] = Field(default_factory=dict)
    confidence_priors: dict[str, dict[str, ConfidencePrior]] = Field(default_factory=dict)
    history: list[dict] = Field(default_factory=list)

    @property
    def source_filename(self) -> str:
        return Path(self.source).name

    def save(self, path: Path) -> None:
        data = self.model_dump(exclude_none=True)
        # Convert to YAML-friendly structure matching spec format
        constraints = {}
        if self.constraints_fd:
            constraints["functional_dependencies"] = [
                fd.model_dump() for fd in self.constraints_fd
            ]
        if self.constraints_keys:
            constraints["candidate_keys"] = self.constraints_keys
        if self.constraints_temporal:
            constraints["temporal_orders"] = [
                t.model_dump() for t in self.constraints_temporal
            ]
        if constraints:
            data["constraints"] = constraints
        # Remove flattened keys
        data.pop("constraints_fd", None)
        data.pop("constraints_keys", None)
        data.pop("constraints_temporal", None)
        # Convert patterns to spec format
        if self.patterns:
            pat_out = {}
            for col, grammars in self.patterns.items():
                pat_out[col] = {
                    "grammars": [g.model_dump() for g in grammars],
                    "total_coverage": sum(g.coverage for g in grammars),
                }
            data["patterns"] = pat_out
        # Convert priors to spec format
        if self.confidence_priors:
            priors_out = {}
            for check, cols in self.confidence_priors.items():
                priors_out[check] = {
                    col: p.model_dump() for col, p in cols.items()
                }
            data["confidence_priors"] = priors_out
        path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))

    @classmethod
    def load(cls, path: Path) -> BaselineProfile:
        raw = yaml.safe_load(path.read_text())
        # Expand constraints section
        constraints = raw.pop("constraints", {})
        raw["constraints_fd"] = [
            FunctionalDependency(**fd)
            for fd in constraints.get("functional_dependencies", [])
        ]
        raw["constraints_keys"] = constraints.get("candidate_keys", [])
        raw["constraints_temporal"] = [
            TemporalOrder(**t) for t in constraints.get("temporal_orders", [])
        ]
        # Expand patterns
        patterns_raw = raw.pop("patterns", {})
        raw["patterns"] = {
            col: [PatternGrammar(**g) for g in v.get("grammars", [])]
            for col, v in patterns_raw.items()
        }
        # Expand priors
        priors_raw = raw.pop("confidence_priors", {})
        raw["confidence_priors"] = {
            check: {col: ConfidencePrior(**p) for col, p in cols.items()}
            for check, cols in priors_raw.items()
        }
        # Filter unknown keys
        known = set(cls.model_fields.keys())
        unknown = set(raw.keys()) - known
        for key in unknown:
            logger.warning("Ignoring unknown baseline key: %s", key)
            raw.pop(key)
        return cls(**raw)
```

- [ ] **Step 5: Run tests — verify they pass**

Run: `cd D:/show_case/goldencheck && python -m pytest tests/baseline/test_models.py -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add goldencheck/baseline/__init__.py goldencheck/baseline/models.py tests/baseline/__init__.py tests/baseline/test_models.py
git commit -m "feat(baseline): add Pydantic models for baseline profiles"
```

---

## Task 2: Optional Dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add baseline and semantic dependency groups**

In `pyproject.toml`, add to `[project.optional-dependencies]`:

```toml
baseline = ["scipy>=1.10", "numpy>=1.24"]
semantic = ["sentence-transformers>=2.0"]
```

- [ ] **Step 2: Install baseline deps locally**

Run: `cd D:/show_case/goldencheck && pip install -e ".[baseline,dev]"`
Expected: scipy and numpy installed

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add baseline and semantic optional dependency groups"
```

---

## Task 3: Statistical Profiler

**Files:**
- Create: `goldencheck/baseline/statistical.py`
- Create: `tests/baseline/test_statistical.py`

- [ ] **Step 1: Write tests**

```python
# tests/baseline/test_statistical.py
"""Tests for statistical profiling — distribution fitting, Benford's, entropy."""
from __future__ import annotations

import numpy as np
import polars as pl
import pytest
from goldencheck.baseline.statistical import profile_statistical
from goldencheck.baseline.models import StatProfile


class TestDistributionFitting:
    def test_normal_distribution(self):
        rng = np.random.default_rng(42)
        data = rng.normal(loc=50, scale=10, size=5000)
        df = pl.DataFrame({"age": data})
        result = profile_statistical(df)
        assert "age" in result
        assert result["age"].distribution == "normal"
        assert abs(result["age"].params["mu"] - 50) < 2
        assert abs(result["age"].params["sigma"] - 10) < 2

    def test_lognormal_distribution(self):
        rng = np.random.default_rng(42)
        data = rng.lognormal(mean=10, sigma=0.5, size=5000)
        df = pl.DataFrame({"income": data})
        result = profile_statistical(df)
        assert "income" in result
        assert result["income"].distribution == "log_normal"

    def test_skips_non_numeric(self):
        df = pl.DataFrame({"name": ["alice", "bob", "charlie"] * 100})
        result = profile_statistical(df)
        assert "name" not in result

    def test_skips_low_row_count(self):
        df = pl.DataFrame({"val": [1.0, 2.0, 3.0]})
        result = profile_statistical(df)
        assert result == {}


class TestEntropy:
    def test_uniform_high_entropy(self):
        df = pl.DataFrame({"cat": [f"val_{i}" for i in range(1000)]})
        result = profile_statistical(df)
        assert "cat" in result
        assert result["cat"].entropy > 5

    def test_single_value_zero_entropy(self):
        df = pl.DataFrame({"status": ["active"] * 500})
        result = profile_statistical(df)
        assert "status" in result
        assert result["status"].entropy < 0.01


class TestBounds:
    def test_percentile_bounds(self):
        rng = np.random.default_rng(42)
        data = rng.normal(loc=100, scale=20, size=5000)
        df = pl.DataFrame({"score": data})
        result = profile_statistical(df)
        assert result["score"].bounds["p01"] < 60
        assert result["score"].bounds["p99"] > 140


class TestBenford:
    def test_benford_eligible_column(self):
        # Populations roughly follow Benford's law
        rng = np.random.default_rng(42)
        data = rng.lognormal(mean=10, sigma=2, size=5000)
        df = pl.DataFrame({"population": data.astype(int).astype(float)})
        result = profile_statistical(df, semantic_types={"amount": ["population"]})
        assert "population" in result
        assert result["population"].benford is not None

    def test_benford_skipped_for_ids(self):
        df = pl.DataFrame({"user_id": list(range(1, 5001))})
        result = profile_statistical(df, semantic_types={"identifier": ["user_id"]})
        if "user_id" in result:
            assert result["user_id"].benford is None
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `cd D:/show_case/goldencheck && python -m pytest tests/baseline/test_statistical.py -v`
Expected: ImportError

- [ ] **Step 3: Implement statistical profiler**

```python
# goldencheck/baseline/statistical.py
"""Statistical profiling — distribution fitting, Benford's Law, entropy."""
from __future__ import annotations

import logging
import math
from collections import Counter

import polars as pl

logger = logging.getLogger(__name__)

MIN_ROWS = 30  # minimum rows to attempt distribution fitting
BENFORD_KEYWORDS = {"amount", "total", "revenue", "population", "count", "price", "salary", "income", "cost", "fee"}

try:
    import numpy as np
    from scipy import stats as sp_stats
except ImportError:
    raise ImportError(
        "Install goldencheck[baseline] for deep profiling: pip install goldencheck[baseline]"
    )

from goldencheck.baseline.models import StatProfile

DISTRIBUTIONS = [
    ("normal", sp_stats.norm),
    ("log_normal", sp_stats.lognorm),
    ("exponential", sp_stats.expon),
    ("uniform", sp_stats.uniform),
]


def profile_statistical(
    df: pl.DataFrame,
    semantic_types: dict[str, list[str]] | None = None,
) -> dict[str, StatProfile]:
    """Profile all columns, returning StatProfile per eligible column."""
    if df.height < MIN_ROWS:
        return {}

    sem = semantic_types or {}
    # Invert: column -> type
    col_type: dict[str, str] = {}
    for stype, cols in sem.items():
        for c in cols:
            col_type[c] = stype

    result: dict[str, StatProfile] = {}
    for col in df.columns:
        series = df[col]

        # Numeric columns: distribution fitting + bounds
        if series.dtype.is_numeric():
            values = series.drop_nulls().to_numpy().astype(float)
            if len(values) < MIN_ROWS:
                continue
            prof = _profile_numeric(col, values, col_type.get(col))
            if prof:
                result[col] = prof

        # Categorical/string columns: entropy only
        elif series.dtype == pl.Utf8 or series.dtype == pl.Categorical:
            vals = series.drop_nulls().to_list()
            if len(vals) < MIN_ROWS:
                continue
            ent = _entropy(vals)
            result[col] = StatProfile(entropy=ent, bounds={})

    return result


def _profile_numeric(col: str, values: np.ndarray, sem_type: str | None) -> StatProfile | None:
    """Fit distributions, compute bounds, and optionally check Benford's."""
    entropy = _entropy_numeric(values)
    bounds = {
        "p01": float(np.percentile(values, 1)),
        "p99": float(np.percentile(values, 99)),
    }

    # Distribution fitting
    best_dist, best_params = _fit_distribution(values)

    # Benford's Law
    benford = None
    if _is_benford_eligible(col, values, sem_type):
        benford = _check_benford(values)

    return StatProfile(
        distribution=best_dist,
        params=best_params,
        benford=benford,
        entropy=entropy,
        bounds=bounds,
    )


def _fit_distribution(values: np.ndarray) -> tuple[str | None, dict[str, float] | None]:
    """Fit candidate distributions and return best by KS-test p-value."""
    best_name = None
    best_p = -1.0
    best_params = None

    positive = values[values > 0]

    for name, dist in DISTRIBUTIONS:
        try:
            data = positive if name == "log_normal" else values
            if len(data) < MIN_ROWS:
                continue
            params = dist.fit(data)
            _, p = sp_stats.kstest(data, dist.cdf, args=params)
            if p > best_p:
                best_p = p
                best_name = name
                if name == "normal":
                    best_params = {"mu": float(params[0]), "sigma": float(params[1])}
                elif name == "log_normal":
                    best_params = {"s": float(params[0]), "loc": float(params[1]), "scale": float(params[2])}
                elif name == "exponential":
                    best_params = {"loc": float(params[0]), "scale": float(params[1])}
                elif name == "uniform":
                    best_params = {"loc": float(params[0]), "scale": float(params[1])}
        except Exception:
            logger.debug("Distribution fit failed for %s on %s", name, "column")
            continue

    if best_p < 0.01:
        return None, None  # no good fit
    return best_name, best_params


def _is_benford_eligible(col: str, values: np.ndarray, sem_type: str | None) -> bool:
    """Check if a column should be tested for Benford's Law."""
    if sem_type in {"identifier", "code", "percentage", "boolean"}:
        return False
    if sem_type in {"amount", "currency", "count"}:
        return True
    # Heuristic: non-negative, spans 2+ orders of magnitude
    positive = values[values > 0]
    if len(positive) < MIN_ROWS:
        return False
    if positive.min() <= 0:
        return False
    magnitude_range = math.log10(positive.max()) - math.log10(positive.min())
    if magnitude_range < 2:
        return False
    # Keyword check
    col_lower = col.lower()
    return any(kw in col_lower for kw in BENFORD_KEYWORDS)


def _check_benford(values: np.ndarray) -> dict[str, float | bool]:
    """Test Benford's Law conformance via chi-squared."""
    positive = values[values > 0]
    leading = [int(str(abs(v)).lstrip("0.")[0]) for v in positive if v != 0]
    leading = [d for d in leading if 1 <= d <= 9]
    if len(leading) < MIN_ROWS:
        return {"passes": False, "chi2": 0.0}
    counts = Counter(leading)
    observed = [counts.get(d, 0) for d in range(1, 10)]
    n = sum(observed)
    expected = [n * math.log10(1 + 1 / d) for d in range(1, 10)]
    chi2, p = sp_stats.chisquare(observed, expected)
    return {"passes": bool(p > 0.05), "chi2": float(chi2)}


def _entropy(values: list) -> float:
    """Shannon entropy of a categorical sequence."""
    if not values:
        return 0.0
    counts = Counter(values)
    n = len(values)
    return -sum((c / n) * math.log2(c / n) for c in counts.values() if c > 0)


def _entropy_numeric(values: np.ndarray) -> float:
    """Approximate entropy for numeric data via histogram binning."""
    if len(values) < 2:
        return 0.0
    n_bins = min(50, int(math.sqrt(len(values))))
    hist, _ = np.histogram(values, bins=n_bins)
    total = hist.sum()
    if total == 0:
        return 0.0
    probs = hist[hist > 0] / total
    return float(-np.sum(probs * np.log2(probs)))
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `cd D:/show_case/goldencheck && python -m pytest tests/baseline/test_statistical.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add goldencheck/baseline/statistical.py tests/baseline/test_statistical.py
git commit -m "feat(baseline): add statistical profiler — distributions, Benford's, entropy"
```

---

## Task 4: Constraint Miner

**Files:**
- Create: `goldencheck/baseline/constraints.py`
- Create: `tests/baseline/test_constraints.py`

- [ ] **Step 1: Write tests**

```python
# tests/baseline/test_constraints.py
"""Tests for constraint mining — functional dependencies and temporal orders."""
from __future__ import annotations

import polars as pl
from goldencheck.baseline.constraints import mine_constraints
from goldencheck.baseline.models import FunctionalDependency, TemporalOrder


class TestFunctionalDependencies:
    def test_discovers_exact_fd(self):
        # zip_code -> city is a perfect FD
        df = pl.DataFrame({
            "zip_code": ["10001"] * 100 + ["90210"] * 100,
            "city": ["New York"] * 100 + ["Beverly Hills"] * 100,
            "name": [f"person_{i}" for i in range(200)],
        })
        fds, _, _ = mine_constraints(df)
        fd_strs = [(tuple(fd.determinant), tuple(fd.dependent)) for fd in fds]
        assert (("zip_code",), ("city",)) in fd_strs

    def test_approximate_fd(self):
        # 98% consistent FD
        cities = ["New York"] * 98 + ["Brooklyn"] * 2  # 2% violations
        df = pl.DataFrame({
            "zip_code": ["10001"] * 100,
            "city": cities,
        })
        fds, _, _ = mine_constraints(df, min_confidence=0.95)
        fd_strs = [(tuple(fd.determinant), tuple(fd.dependent)) for fd in fds]
        assert (("zip_code",), ("city",)) in fd_strs

    def test_no_fd_on_random_data(self):
        import random
        rng = random.Random(42)
        df = pl.DataFrame({
            "a": [rng.choice(["x", "y", "z"]) for _ in range(500)],
            "b": [rng.choice(["1", "2", "3"]) for _ in range(500)],
        })
        fds, _, _ = mine_constraints(df)
        assert len(fds) == 0

    def test_respects_column_limit(self):
        # 40 low-cardinality columns — should only process 30
        data = {}
        for i in range(40):
            data[f"col_{i}"] = [str(v % 5) for v in range(200)]
        df = pl.DataFrame(data)
        fds, _, _ = mine_constraints(df)
        # Should complete without error (not blow up)
        assert isinstance(fds, list)


class TestCandidateKeys:
    def test_unique_column_detected(self):
        df = pl.DataFrame({
            "id": list(range(100)),
            "name": [f"name_{i}" for i in range(100)],
            "category": ["a", "b"] * 50,
        })
        _, keys, _ = mine_constraints(df)
        key_cols = [tuple(k["columns"]) for k in keys]
        assert ("id",) in key_cols


class TestTemporalOrders:
    def test_discovers_date_order(self):
        df = pl.DataFrame({
            "start_date": ["2024-01-01", "2024-02-01", "2024-03-01"] * 100,
            "end_date": ["2024-06-01", "2024-07-01", "2024-08-01"] * 100,
        })
        _, _, temporals = mine_constraints(
            df, date_columns=["start_date", "end_date"],
        )
        assert len(temporals) >= 1
        assert temporals[0].before == "start_date"
        assert temporals[0].after == "end_date"
        assert temporals[0].violation_rate == 0.0

    def test_records_violations(self):
        starts = ["2024-01-01"] * 95 + ["2024-12-01"] * 5
        ends = ["2024-06-01"] * 95 + ["2024-01-01"] * 5
        df = pl.DataFrame({"start": starts, "end": ends})
        _, _, temporals = mine_constraints(df, date_columns=["start", "end"])
        assert len(temporals) == 1
        assert abs(temporals[0].violation_rate - 0.05) < 0.01
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `cd D:/show_case/goldencheck && python -m pytest tests/baseline/test_constraints.py -v`
Expected: ImportError

- [ ] **Step 3: Implement constraint miner**

```python
# goldencheck/baseline/constraints.py
"""Constraint mining — functional dependency discovery and temporal orders."""
from __future__ import annotations

import logging
from itertools import combinations

import polars as pl

from goldencheck.baseline.models import FunctionalDependency, TemporalOrder

logger = logging.getLogger(__name__)

MAX_FD_COLUMNS = 30
MAX_CARDINALITY = 1000
MIN_ROWS = 30


def mine_constraints(
    df: pl.DataFrame,
    min_confidence: float = 0.95,
    date_columns: list[str] | None = None,
) -> tuple[list[FunctionalDependency], list[dict], list[TemporalOrder]]:
    """Discover functional dependencies, candidate keys, and temporal orders.

    Returns (fds, candidate_keys, temporal_orders).
    """
    if df.height < MIN_ROWS:
        return [], [], []

    fds = _mine_fds(df, min_confidence)
    keys = _find_candidate_keys(df)
    temporals = _find_temporal_orders(df, date_columns or [])
    return fds, keys, temporals


def _mine_fds(
    df: pl.DataFrame, min_confidence: float,
) -> list[FunctionalDependency]:
    """Simplified TANE: check single-column determinants only."""
    # Filter to low-cardinality columns
    eligible = []
    for col in df.columns:
        n_unique = df[col].n_unique()
        if n_unique < MAX_CARDINALITY:
            eligible.append((col, n_unique))

    # Sort by cardinality (lowest first) and limit
    eligible.sort(key=lambda x: x[1])
    eligible = eligible[:MAX_FD_COLUMNS]
    col_names = [c for c, _ in eligible]

    fds = []
    for det_col in col_names:
        for dep_col in col_names:
            if det_col == dep_col:
                continue
            conf = _fd_confidence(df, det_col, dep_col)
            if conf >= min_confidence:
                fds.append(FunctionalDependency(
                    determinant=[det_col],
                    dependent=[dep_col],
                    confidence=round(conf, 4),
                ))

    # Merge: if A->B and A->C, combine to A->[B,C]
    return _merge_fds(fds)


def _fd_confidence(df: pl.DataFrame, det: str, dep: str) -> float:
    """Fraction of groups where determinant uniquely determines dependent."""
    grouped = df.group_by(det).agg(pl.col(dep).n_unique().alias("n_dep"))
    consistent = grouped.filter(pl.col("n_dep") == 1).height
    total = grouped.height
    if total == 0:
        return 0.0
    return consistent / total


def _merge_fds(fds: list[FunctionalDependency]) -> list[FunctionalDependency]:
    """Merge FDs with the same determinant."""
    from collections import defaultdict
    groups: dict[tuple, list[tuple[str, float]]] = defaultdict(list)
    for fd in fds:
        key = tuple(fd.determinant)
        for dep in fd.dependent:
            groups[key].append((dep, fd.confidence))

    merged = []
    for det, deps in groups.items():
        min_conf = min(c for _, c in deps)
        merged.append(FunctionalDependency(
            determinant=list(det),
            dependent=[d for d, _ in deps],
            confidence=round(min_conf, 4),
        ))
    return merged


def _find_candidate_keys(df: pl.DataFrame) -> list[dict]:
    """Find single columns that are candidate keys (100% unique, no nulls)."""
    keys = []
    for col in df.columns:
        series = df[col]
        if series.null_count() == 0 and series.n_unique() == df.height:
            keys.append({"columns": [col], "unique": True})
    return keys


def _find_temporal_orders(
    df: pl.DataFrame, date_columns: list[str],
) -> list[TemporalOrder]:
    """Check all date column pairs for temporal ordering."""
    if len(date_columns) < 2:
        return []

    temporals = []
    for before, after in combinations(date_columns, 2):
        try:
            before_dates = df[before].cast(pl.Date)
            after_dates = df[after].cast(pl.Date)
        except Exception:
            continue

        mask = before_dates > after_dates
        non_null = mask.drop_nulls()
        if non_null.len() == 0:
            continue
        violation_rate = non_null.sum() / non_null.len()

        if violation_rate < 0.5:  # more often ordered than not
            temporals.append(TemporalOrder(
                before=before,
                after=after,
                violation_rate=round(float(violation_rate), 4),
            ))
        elif violation_rate > 0.5:  # reverse order
            temporals.append(TemporalOrder(
                before=after,
                after=before,
                violation_rate=round(float(1 - violation_rate), 4),
            ))

    return temporals
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `cd D:/show_case/goldencheck && python -m pytest tests/baseline/test_constraints.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add goldencheck/baseline/constraints.py tests/baseline/test_constraints.py
git commit -m "feat(baseline): add constraint miner — FDs, candidate keys, temporal orders"
```

---

## Task 5: Semantic Type Inferrer

**Files:**
- Create: `goldencheck/baseline/semantic.py`
- Create: `tests/baseline/test_semantic.py`

- [ ] **Step 1: Write tests**

```python
# tests/baseline/test_semantic.py
"""Tests for semantic type inference — embeddings with keyword fallback."""
from __future__ import annotations

import polars as pl
from goldencheck.baseline.semantic import infer_semantic_types


class TestKeywordFallback:
    """These tests work without sentence-transformers installed."""

    def test_email_column(self):
        df = pl.DataFrame({
            "email": ["alice@example.com", "bob@test.org", "carol@foo.net"] * 50,
        })
        result = infer_semantic_types(df)
        assert "email" in result
        assert result["email"] == ["email"]

    def test_multiple_columns_same_type(self):
        df = pl.DataFrame({
            "phone": ["555-1234"] * 100,
            "mobile_phone": ["555-5678"] * 100,
        })
        result = infer_semantic_types(df)
        assert "phone" in result
        assert set(result["phone"]) == {"phone", "mobile_phone"}

    def test_date_columns(self):
        df = pl.DataFrame({
            "start_date": ["2024-01-01"] * 100,
            "end_date": ["2024-06-01"] * 100,
            "name": ["Alice"] * 100,
        })
        result = infer_semantic_types(df)
        assert "date" in result
        assert "start_date" in result["date"]
        assert "end_date" in result["date"]

    def test_unclassifiable_column(self):
        df = pl.DataFrame({"xq7_val": [1.0, 2.0, 3.0] * 100})
        result = infer_semantic_types(df)
        # Columns with no match should not appear
        classified_cols = {c for cols in result.values() for c in cols}
        assert "xq7_val" not in classified_cols
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `cd D:/show_case/goldencheck && python -m pytest tests/baseline/test_semantic.py -v`
Expected: ImportError

- [ ] **Step 3: Implement semantic inferrer**

```python
# goldencheck/baseline/semantic.py
"""Semantic type inference — embeddings with keyword fallback."""
from __future__ import annotations

import logging
from collections import defaultdict

import polars as pl

logger = logging.getLogger(__name__)

# Keyword mapping for fallback (no embedding model needed)
KEYWORD_TYPES: dict[str, list[str]] = {
    "email": ["email", "e_mail", "mail_address"],
    "phone": ["phone", "tel", "mobile", "fax", "cell"],
    "person_name": ["first_name", "last_name", "full_name", "name", "given_name", "surname"],
    "address": ["address", "street", "addr", "address_line"],
    "date": ["date", "datetime", "timestamp", "created_at", "updated_at", "dob", "birth_date"],
    "currency": ["amount", "price", "cost", "salary", "fee", "total", "revenue", "income", "bonus"],
    "identifier": ["_id", "uuid", "key", "code", "sku", "ref"],
    "category": ["status", "type", "category", "level", "tier", "role"],
    "percentage": ["pct", "percent", "rate", "ratio"],
    "boolean": ["is_", "has_", "flag", "enabled", "active"],
    "geo": ["city", "state", "country", "zip", "postal", "region", "county", "province", "latitude", "longitude"],
    "url": ["url", "link", "href", "website", "uri"],
    "ssn": ["ssn", "social_security"],
}


def infer_semantic_types(
    df: pl.DataFrame,
    use_embeddings: bool = True,
) -> dict[str, list[str]]:
    """Infer semantic type for each column. Returns {type: [columns]}.

    Tries embedding-based inference first (if sentence-transformers installed),
    falls back to keyword matching.
    """
    if use_embeddings:
        try:
            return _infer_with_embeddings(df)
        except ImportError:
            logger.info("sentence-transformers not installed, falling back to keyword matching")

    return _infer_with_keywords(df)


def _infer_with_keywords(df: pl.DataFrame) -> dict[str, list[str]]:
    """Classify columns by matching column names against keyword lists."""
    result: dict[str, list[str]] = defaultdict(list)

    for col in df.columns:
        col_lower = col.lower()
        for sem_type, keywords in KEYWORD_TYPES.items():
            for kw in keywords:
                if kw.endswith("_"):
                    # Prefix match
                    if col_lower.startswith(kw):
                        result[sem_type].append(col)
                        break
                elif kw.startswith("_"):
                    # Suffix match
                    if col_lower.endswith(kw):
                        result[sem_type].append(col)
                        break
                else:
                    # Substring match
                    if kw in col_lower:
                        result[sem_type].append(col)
                        break
            else:
                continue
            break  # matched — don't check more types

    return dict(result)


def _infer_with_embeddings(df: pl.DataFrame) -> dict[str, list[str]]:
    """Classify columns using sentence-transformer embeddings."""
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer("all-MiniLM-L6-v2")

    # Build type exemplars
    type_labels = list(KEYWORD_TYPES.keys())
    type_descriptions = [
        f"{t}: " + ", ".join(KEYWORD_TYPES[t][:5]) for t in type_labels
    ]
    type_embeddings = model.encode(type_descriptions, normalize_embeddings=True)

    result: dict[str, list[str]] = defaultdict(list)

    for col in df.columns:
        # Build column description from name + sample values
        sample_vals = df[col].drop_nulls().head(10).to_list()
        desc = f"column '{col}' with values: {', '.join(str(v) for v in sample_vals[:5])}"
        col_embedding = model.encode([desc], normalize_embeddings=True)

        # Cosine similarity (embeddings are normalized, so dot product = cosine)
        similarities = (col_embedding @ type_embeddings.T)[0]
        best_idx = similarities.argmax()
        best_score = similarities[best_idx]

        if best_score > 0.3:  # threshold for classification
            result[type_labels[best_idx]].append(col)

    return dict(result)
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `cd D:/show_case/goldencheck && python -m pytest tests/baseline/test_semantic.py -v`
Expected: All pass (keyword fallback works without sentence-transformers)

- [ ] **Step 5: Commit**

```bash
git add goldencheck/baseline/semantic.py tests/baseline/test_semantic.py
git commit -m "feat(baseline): add semantic type inferrer with embedding/keyword fallback"
```

---

## Task 6: Correlation Analyzer

**Files:**
- Create: `goldencheck/baseline/correlation.py`
- Create: `tests/baseline/test_correlation.py`

- [ ] **Step 1: Write tests**

```python
# tests/baseline/test_correlation.py
"""Tests for correlation analysis — mutual info, Cramer's V, Pearson."""
from __future__ import annotations

import numpy as np
import polars as pl
from goldencheck.baseline.correlation import analyze_correlations
from goldencheck.baseline.models import CorrelationEntry


class TestNumericCorrelation:
    def test_perfect_correlation(self):
        df = pl.DataFrame({
            "x": list(range(500)),
            "y": [v * 2 + 1 for v in range(500)],
        })
        result = analyze_correlations(df)
        strong = [c for c in result if c.strength == "strong"]
        assert len(strong) >= 1
        pair_cols = {tuple(sorted(c.columns)) for c in strong}
        assert ("x", "y") in pair_cols

    def test_no_correlation(self):
        rng = np.random.default_rng(42)
        df = pl.DataFrame({
            "x": rng.normal(size=500).tolist(),
            "y": rng.normal(size=500).tolist(),
        })
        result = analyze_correlations(df)
        strong = [c for c in result if c.strength == "strong"]
        assert len(strong) == 0


class TestCategoricalCorrelation:
    def test_cramers_v_strong(self):
        # Perfectly correlated categories
        df = pl.DataFrame({
            "city": ["NYC", "LA", "Chicago"] * 200,
            "state": ["NY", "CA", "IL"] * 200,
        })
        result = analyze_correlations(df)
        strong = [c for c in result if c.strength == "strong" and c.measure == "cramers_v"]
        assert len(strong) >= 1


class TestMixedTypes:
    def test_skips_too_many_columns(self):
        # 50 columns — should not blow up
        data = {f"col_{i}": list(range(100)) for i in range(50)}
        df = pl.DataFrame(data)
        result = analyze_correlations(df)
        assert isinstance(result, list)
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `cd D:/show_case/goldencheck && python -m pytest tests/baseline/test_correlation.py -v`
Expected: ImportError

- [ ] **Step 3: Implement correlation analyzer**

```python
# goldencheck/baseline/correlation.py
"""Correlation analysis — mutual info, Cramer's V, Pearson/Spearman."""
from __future__ import annotations

import logging
from itertools import combinations

import polars as pl

logger = logging.getLogger(__name__)

MAX_PAIRS = 500  # limit column pairs to avoid O(n^2) blowup

try:
    import numpy as np
    from scipy import stats as sp_stats
except ImportError:
    raise ImportError(
        "Install goldencheck[baseline] for deep profiling: pip install goldencheck[baseline]"
    )

from goldencheck.baseline.models import CorrelationEntry


def analyze_correlations(df: pl.DataFrame) -> list[CorrelationEntry]:
    """Analyze pairwise column correlations."""
    results: list[CorrelationEntry] = []

    numeric_cols = [c for c in df.columns if df[c].dtype.is_numeric()]
    string_cols = [c for c in df.columns if df[c].dtype == pl.Utf8]

    # Numeric-numeric: Pearson
    for a, b in _limited_pairs(numeric_cols):
        entry = _numeric_correlation(df, a, b)
        if entry:
            results.append(entry)

    # Categorical-categorical: Cramer's V
    # Only use low-cardinality strings
    cat_cols = [c for c in string_cols if df[c].n_unique() < 100]
    for a, b in _limited_pairs(cat_cols):
        entry = _cramers_v(df, a, b)
        if entry:
            results.append(entry)

    return results


def _limited_pairs(cols: list[str]) -> list[tuple[str, str]]:
    """Return column pairs, limited to MAX_PAIRS."""
    pairs = list(combinations(cols, 2))
    if len(pairs) > MAX_PAIRS:
        logger.info("Limiting correlation pairs from %d to %d", len(pairs), MAX_PAIRS)
        return pairs[:MAX_PAIRS]
    return pairs


def _numeric_correlation(df: pl.DataFrame, a: str, b: str) -> CorrelationEntry | None:
    """Compute Pearson correlation between two numeric columns."""
    clean = df.select([a, b]).drop_nulls()
    if clean.height < 30:
        return None

    va = clean[a].to_numpy().astype(float)
    vb = clean[b].to_numpy().astype(float)

    r, _ = sp_stats.pearsonr(va, vb)
    strength = _strength(abs(r))

    if strength == "weak":
        return None

    return CorrelationEntry(
        columns=[a, b],
        measure="pearson",
        value=round(float(r), 4),
        strength=strength,
    )


def _cramers_v(df: pl.DataFrame, a: str, b: str) -> CorrelationEntry | None:
    """Compute Cramer's V between two categorical columns."""
    clean = df.select([a, b]).drop_nulls()
    if clean.height < 30:
        return None

    # Build contingency table
    ct = clean.group_by([a, b]).len().pivot(on=b, index=a, values="len").fill_null(0)
    # Extract numeric matrix
    mat = ct.select([c for c in ct.columns if c != a]).to_numpy()

    chi2, _, _, _ = sp_stats.chi2_contingency(mat)
    n = mat.sum()
    min_dim = min(mat.shape[0], mat.shape[1]) - 1
    if min_dim == 0 or n == 0:
        return None

    v = float(np.sqrt(chi2 / (n * min_dim)))
    strength = _strength(v)

    if strength == "weak":
        return None

    return CorrelationEntry(
        columns=[a, b],
        measure="cramers_v",
        value=round(v, 4),
        strength=strength,
    )


def _strength(value: float) -> str:
    if abs(value) >= 0.7:
        return "strong"
    if abs(value) >= 0.4:
        return "moderate"
    return "weak"
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `cd D:/show_case/goldencheck && python -m pytest tests/baseline/test_correlation.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add goldencheck/baseline/correlation.py tests/baseline/test_correlation.py
git commit -m "feat(baseline): add correlation analyzer — Pearson, Cramer's V"
```

---

## Task 7: Pattern Grammar Inducer

**Files:**
- Create: `goldencheck/baseline/patterns.py`
- Create: `tests/baseline/test_patterns.py`

- [ ] **Step 1: Write tests**

```python
# tests/baseline/test_patterns.py
"""Tests for pattern grammar induction."""
from __future__ import annotations

import polars as pl
from goldencheck.baseline.patterns import induce_patterns
from goldencheck.baseline.models import PatternGrammar


class TestPatternInduction:
    def test_simple_product_code(self):
        codes = [f"AB{i:04d}" for i in range(500)]
        df = pl.DataFrame({"code": codes})
        result = induce_patterns(df)
        assert "code" in result
        assert len(result["code"]) >= 1
        assert result["code"][0].coverage > 0.9

    def test_multiple_formats(self):
        phones = (
            ["(555) 123-4567"] * 80
            + ["555-123-4567"] * 20
        )
        df = pl.DataFrame({"phone": phones})
        result = induce_patterns(df)
        assert "phone" in result
        assert len(result["phone"]) >= 2

    def test_skips_numeric_columns(self):
        df = pl.DataFrame({"amount": [1.5, 2.3, 4.7] * 100})
        result = induce_patterns(df)
        assert "amount" not in result

    def test_skips_high_cardinality(self):
        df = pl.DataFrame({"uuid": [f"unique-{i}" for i in range(1000)]})
        result = induce_patterns(df)
        # High cardinality strings with no repeating structure may or may not produce patterns
        # Just verify it doesn't crash
        assert isinstance(result, dict)

    def test_skips_low_row_count(self):
        df = pl.DataFrame({"code": ["ABC-1234"]})
        result = induce_patterns(df)
        assert result == {}
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `cd D:/show_case/goldencheck && python -m pytest tests/baseline/test_patterns.py -v`
Expected: ImportError

- [ ] **Step 3: Implement pattern inducer**

```python
# goldencheck/baseline/patterns.py
"""Pattern grammar induction — learn value formats from data."""
from __future__ import annotations

import logging
import re
from collections import Counter

import polars as pl

from goldencheck.baseline.models import PatternGrammar

logger = logging.getLogger(__name__)

MIN_ROWS = 30
MIN_COVERAGE = 0.03  # minimum 3% coverage to report a grammar


def induce_patterns(df: pl.DataFrame) -> dict[str, list[PatternGrammar]]:
    """Learn structural grammars for string columns."""
    if df.height < MIN_ROWS:
        return {}

    result: dict[str, list[PatternGrammar]] = {}

    for col in df.columns:
        if df[col].dtype != pl.Utf8:
            continue

        values = df[col].drop_nulls().to_list()
        if len(values) < MIN_ROWS:
            continue

        grammars = _induce_column_grammars(values)
        if grammars:
            result[col] = grammars

    return result


def _induce_column_grammars(values: list[str]) -> list[PatternGrammar]:
    """Induce grammars for a single column's values."""
    # Step 1: Convert each value to a character-class skeleton
    skeletons: Counter[str] = Counter()
    for v in values:
        skel = _to_skeleton(v)
        skeletons[skel] += 1

    total = len(values)

    # Step 2: Group similar skeletons and build regex patterns
    grammars = []
    for skel, count in skeletons.most_common():
        coverage = count / total
        if coverage < MIN_COVERAGE:
            continue
        pattern = _skeleton_to_regex(skel)
        grammars.append(PatternGrammar(pattern=pattern, coverage=round(coverage, 4)))

    # Step 3: Merge near-identical patterns
    return _merge_grammars(grammars)


def _to_skeleton(value: str) -> str:
    """Convert a value to a character-class skeleton.

    Examples:
        "ABC-1234" -> "AAA-0000"
        "(555) 123-4567" -> "(000) 000-0000"
    """
    result = []
    for ch in value:
        if ch.isalpha() and ch.isupper():
            result.append("A")
        elif ch.isalpha():
            result.append("a")
        elif ch.isdigit():
            result.append("0")
        else:
            result.append(ch)
    return "".join(result)


def _skeleton_to_regex(skeleton: str) -> str:
    """Convert a skeleton to a regex pattern.

    Examples:
        "AAA-0000" -> "[A-Z]{3}-[0-9]{4}"
        "(000) 000-0000" -> "\\([0-9]{3}\\) [0-9]{3}-[0-9]{4}"
    """
    result = []
    i = 0
    while i < len(skeleton):
        ch = skeleton[i]
        if ch in ("A", "a", "0"):
            # Count consecutive same class
            cls = ch
            count = 0
            while i < len(skeleton) and skeleton[i] == cls:
                count += 1
                i += 1
            if cls == "A":
                result.append(f"[A-Z]{{{count}}}")
            elif cls == "a":
                result.append(f"[a-z]{{{count}}}")
            else:
                result.append(f"[0-9]{{{count}}}")
        else:
            # Literal character (escape regex specials)
            result.append(re.escape(ch))
            i += 1
    return "".join(result)


def _merge_grammars(grammars: list[PatternGrammar]) -> list[PatternGrammar]:
    """Merge grammars with identical patterns, sum their coverage."""
    merged: dict[str, float] = {}
    for g in grammars:
        merged[g.pattern] = merged.get(g.pattern, 0) + g.coverage

    return [
        PatternGrammar(pattern=p, coverage=round(c, 4))
        for p, c in sorted(merged.items(), key=lambda x: -x[1])
    ]
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `cd D:/show_case/goldencheck && python -m pytest tests/baseline/test_patterns.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add goldencheck/baseline/patterns.py tests/baseline/test_patterns.py
git commit -m "feat(baseline): add pattern grammar inducer"
```

---

## Task 8: Confidence Prior Builder

**Files:**
- Create: `goldencheck/baseline/priors.py`
- Create: `tests/baseline/test_priors.py`

- [ ] **Step 1: Write tests**

```python
# tests/baseline/test_priors.py
"""Tests for confidence prior builder."""
from __future__ import annotations

from goldencheck.baseline.priors import build_priors, apply_prior
from goldencheck.baseline.models import ConfidencePrior
from goldencheck.models.finding import Finding, Severity


class TestBuildPriors:
    def test_builds_from_findings(self):
        findings = [
            Finding(severity=Severity.WARNING, column="email", check="format_detection",
                    message="test", confidence=0.9),
            Finding(severity=Severity.WARNING, column="email", check="format_detection",
                    message="test2", confidence=0.95),
            Finding(severity=Severity.ERROR, column="age", check="range_distribution",
                    message="test", confidence=0.7),
        ]
        priors = build_priors(findings, row_count=5000)
        assert "format_detection" in priors
        assert "email" in priors["format_detection"]
        assert priors["format_detection"]["email"].confidence > 0

    def test_empty_findings(self):
        priors = build_priors([], row_count=100)
        assert priors == {}


class TestApplyPrior:
    def test_adjusts_confidence(self):
        prior = ConfidencePrior(confidence=0.9, evidence_count=5000)
        adjusted = apply_prior(raw_confidence=0.5, prior=prior)
        # Should move toward prior (0.9)
        assert adjusted > 0.5
        assert adjusted < 0.9

    def test_weak_prior_small_effect(self):
        prior = ConfidencePrior(confidence=0.9, evidence_count=10)
        adjusted = apply_prior(raw_confidence=0.5, prior=prior)
        # Low evidence count — small adjustment
        assert adjusted > 0.5
        assert adjusted < 0.6

    def test_strong_prior_large_effect(self):
        prior = ConfidencePrior(confidence=0.9, evidence_count=10000)
        adjusted = apply_prior(raw_confidence=0.5, prior=prior)
        # High evidence count (capped at 1.0 weight) — significant adjustment
        assert adjusted > 0.6
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `cd D:/show_case/goldencheck && python -m pytest tests/baseline/test_priors.py -v`
Expected: ImportError

- [ ] **Step 3: Implement prior builder**

```python
# goldencheck/baseline/priors.py
"""Confidence prior builder — calibration from baseline findings."""
from __future__ import annotations

from collections import defaultdict

from goldencheck.baseline.models import ConfidencePrior
from goldencheck.models.finding import Finding


def build_priors(
    findings: list[Finding],
    row_count: int,
) -> dict[str, dict[str, ConfidencePrior]]:
    """Build per-check, per-column confidence priors from baseline findings.

    Returns: {check_name: {column: ConfidencePrior}}
    """
    # Group findings by (check, column)
    groups: dict[tuple[str, str], list[float]] = defaultdict(list)
    for f in findings:
        groups[(f.check, f.column)].append(f.confidence)

    result: dict[str, dict[str, ConfidencePrior]] = defaultdict(dict)
    for (check, column), confidences in groups.items():
        avg_conf = sum(confidences) / len(confidences)
        result[check][column] = ConfidencePrior(
            confidence=round(avg_conf, 4),
            evidence_count=row_count,
        )

    return dict(result)


def apply_prior(raw_confidence: float, prior: ConfidencePrior) -> float:
    """Adjust a raw confidence score using a baseline prior.

    Formula: adjusted = (raw * evidence_weight + prior * prior_weight) / (evidence_weight + prior_weight)
    Where prior_weight = min(evidence_count / 100, 1.0)
    """
    evidence_weight = 1.0
    prior_weight = min(prior.evidence_count / 100, 1.0)

    adjusted = (
        (raw_confidence * evidence_weight + prior.confidence * prior_weight)
        / (evidence_weight + prior_weight)
    )
    return round(min(max(adjusted, 0.0), 1.0), 4)
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `cd D:/show_case/goldencheck && python -m pytest tests/baseline/test_priors.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add goldencheck/baseline/priors.py tests/baseline/test_priors.py
git commit -m "feat(baseline): add confidence prior builder"
```

---

## Task 9: Baseline Creation (Orchestrator)

**Files:**
- Modify: `goldencheck/baseline/__init__.py`
- Create: `tests/baseline/test_create_baseline.py`

- [ ] **Step 1: Write tests**

```python
# tests/baseline/test_create_baseline.py
"""Tests for end-to-end baseline creation."""
from __future__ import annotations

from pathlib import Path
import polars as pl
from goldencheck.baseline import create_baseline, load_baseline


class TestCreateBaseline:
    def test_creates_from_dataframe(self):
        df = pl.DataFrame({
            "id": list(range(500)),
            "email": [f"user{i}@example.com" for i in range(500)],
            "age": [20 + (i % 60) for i in range(500)],
            "city": ["NYC", "LA", "Chicago", "Boston", "Seattle"] * 100,
            "state": ["NY", "CA", "IL", "MA", "WA"] * 100,
        })
        baseline = create_baseline(df, source="test.csv")
        assert baseline.rows == 500
        assert baseline.columns == 5
        assert len(baseline.statistical_profiles) > 0
        assert len(baseline.semantic_types) > 0

    def test_save_and_load(self, tmp_path: Path):
        df = pl.DataFrame({
            "id": list(range(200)),
            "name": [f"person_{i}" for i in range(200)],
        })
        baseline = create_baseline(df, source="data.csv")
        out = tmp_path / "baseline.yaml"
        baseline.save(out)

        loaded = load_baseline(out)
        assert loaded.rows == baseline.rows
        assert loaded.source == "data.csv"

    def test_skip_techniques(self):
        df = pl.DataFrame({
            "x": list(range(200)),
            "y": [f"val_{i}" for i in range(200)],
        })
        baseline = create_baseline(df, source="test.csv", skip=["correlation", "semantic"])
        assert baseline.correlations == []
        assert baseline.semantic_types == {}

    def test_source_filename_property(self):
        df = pl.DataFrame({"x": list(range(100))})
        baseline = create_baseline(df, source="path/to/data.csv")
        assert baseline.source_filename == "data.csv"
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `cd D:/show_case/goldencheck && python -m pytest tests/baseline/test_create_baseline.py -v`
Expected: ImportError — `create_baseline` not defined

- [ ] **Step 3: Implement orchestrator**

```python
# goldencheck/baseline/__init__.py
"""Deep profiling baseline — learn-once, monitor-forever."""
from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)


def create_baseline(
    df_or_path: pl.DataFrame | Path | str,
    *,
    source: str = "",
    skip: list[str] | None = None,
    sample_size: int = 500_000,
) -> "BaselineProfile":
    """Run all 6 deep analysis techniques and build a BaselineProfile.

    Args:
        df_or_path: DataFrame or path to CSV/Parquet file.
        source: Source file identifier for the baseline metadata.
        skip: List of techniques to skip. Valid: statistical, constraints,
              semantic, correlation, patterns, priors.
        sample_size: Max rows to process (default 500K).
    """
    try:
        import numpy  # noqa: F401
        import scipy  # noqa: F401
    except ImportError:
        raise ImportError(
            "Install goldencheck[baseline] for deep profiling: "
            "pip install goldencheck[baseline]"
        )

    from goldencheck.baseline.models import BaselineProfile
    from goldencheck.baseline.semantic import infer_semantic_types
    from goldencheck.baseline.statistical import profile_statistical
    from goldencheck.baseline.constraints import mine_constraints
    from goldencheck.baseline.correlation import analyze_correlations
    from goldencheck.baseline.patterns import induce_patterns
    from goldencheck.baseline.priors import build_priors

    skip_set = set(skip or [])

    # Load data if path
    if isinstance(df_or_path, (str, Path)):
        from goldencheck.engine.reader import read_file
        source = source or str(df_or_path)
        df = read_file(Path(df_or_path))
    else:
        df = df_or_path

    # Sample if needed
    if df.height > sample_size:
        from goldencheck.engine.sampler import maybe_sample
        df = maybe_sample(df, max_rows=sample_size)

    # 1. Semantic types (runs first — others depend on it)
    semantic_types: dict[str, list[str]] = {}
    if "semantic" not in skip_set:
        logger.info("Running semantic type inference...")
        semantic_types = infer_semantic_types(df, use_embeddings=True)

    # 2. Statistical profiles
    stat_profiles = {}
    if "statistical" not in skip_set:
        logger.info("Running statistical profiler...")
        stat_profiles = profile_statistical(df, semantic_types=semantic_types)

    # 3. Constraints
    fds, keys, temporals = [], [], []
    if "constraints" not in skip_set:
        logger.info("Mining constraints...")
        date_cols = semantic_types.get("date", [])
        fds, keys, temporals = mine_constraints(df, date_columns=date_cols)

    # 4. Correlations
    correlations = []
    if "correlation" not in skip_set:
        logger.info("Analyzing correlations...")
        correlations = analyze_correlations(df)

    # 5. Patterns
    patterns = {}
    if "patterns" not in skip_set:
        logger.info("Inducing pattern grammars...")
        patterns = induce_patterns(df)

    # 6. Priors (runs last — uses all findings)
    # Build synthetic findings from other techniques for priors
    confidence_priors: dict = {}
    if "priors" not in skip_set:
        logger.info("Building confidence priors...")
        # Use a quick scan to get baseline findings for priors
        from goldencheck.engine.scanner import scan_file as _scan
        if isinstance(df_or_path, (str, Path)):
            findings, _ = _scan(Path(df_or_path) if isinstance(df_or_path, str) else df_or_path)
        else:
            # Can't run scan_file without a path — skip priors for DataFrame input
            findings = []
        confidence_priors = build_priors(findings, row_count=df.height)

    return BaselineProfile(
        source=source,
        rows=df.height,
        columns=df.width,
        statistical_profiles=stat_profiles,
        constraints_fd=fds,
        constraints_keys=keys,
        constraints_temporal=temporals,
        semantic_types=semantic_types,
        correlations=correlations,
        patterns=patterns,
        confidence_priors=confidence_priors,
    )


def load_baseline(path: Path | str) -> "BaselineProfile":
    """Load a baseline profile from a YAML file."""
    from goldencheck.baseline.models import BaselineProfile
    return BaselineProfile.load(Path(path))
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `cd D:/show_case/goldencheck && python -m pytest tests/baseline/test_create_baseline.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add goldencheck/baseline/__init__.py tests/baseline/test_create_baseline.py
git commit -m "feat(baseline): add create_baseline orchestrator"
```

---

## Task 10: Drift Detector

**Files:**
- Create: `goldencheck/drift/__init__.py`
- Create: `goldencheck/drift/detector.py`
- Create: `tests/drift/__init__.py`
- Create: `tests/drift/test_detector.py`

- [ ] **Step 1: Write tests**

```python
# tests/drift/test_detector.py
"""Tests for drift detection against baselines."""
from __future__ import annotations

import numpy as np
import polars as pl
from goldencheck.baseline.models import (
    BaselineProfile, StatProfile, FunctionalDependency,
    PatternGrammar, CorrelationEntry,
)
from goldencheck.drift import run_drift_checks
from goldencheck.models.finding import Severity


class TestStatisticalDrift:
    def test_detects_distribution_shift(self):
        baseline = BaselineProfile(
            rows=5000, columns=1,
            statistical_profiles={
                "income": StatProfile(
                    distribution="normal",
                    params={"mu": 50000, "sigma": 10000},
                    entropy=5.0,
                    bounds={"p01": 25000, "p99": 75000},
                ),
            },
        )
        # New data with very different distribution
        rng = np.random.default_rng(42)
        df = pl.DataFrame({"income": rng.normal(loc=100000, scale=5000, size=1000).tolist()})
        findings = run_drift_checks(df, baseline)
        drift_findings = [f for f in findings if f.check == "distribution_drift"]
        assert len(drift_findings) >= 1

    def test_detects_bound_violation(self):
        baseline = BaselineProfile(
            rows=1000, columns=1,
            statistical_profiles={
                "age": StatProfile(
                    entropy=3.0,
                    bounds={"p01": 18, "p99": 95},
                ),
            },
        )
        df = pl.DataFrame({"age": [200, 300, 500] + [30] * 500})
        findings = run_drift_checks(df, baseline)
        bound_findings = [f for f in findings if f.check == "bound_violation"]
        assert len(bound_findings) >= 1


class TestConstraintDrift:
    def test_detects_fd_violation(self):
        baseline = BaselineProfile(
            rows=1000, columns=2,
            constraints_fd=[
                FunctionalDependency(
                    determinant=["zip"], dependent=["city"], confidence=1.0,
                ),
            ],
        )
        # New data breaks the FD
        df = pl.DataFrame({
            "zip": ["10001"] * 100,
            "city": ["New York"] * 90 + ["Brooklyn"] * 10,
        })
        findings = run_drift_checks(df, baseline)
        fd_findings = [f for f in findings if f.check == "fd_violation"]
        assert len(fd_findings) >= 1


class TestPatternDrift:
    def test_detects_coverage_drop(self):
        baseline = BaselineProfile(
            rows=1000, columns=1,
            patterns={
                "code": [PatternGrammar(pattern="[A-Z]{3}-[0-9]{4}", coverage=0.98)],
            },
        )
        # New data: only 50% match the pattern
        codes = ["ABC-1234"] * 50 + ["invalid!!"] * 50
        df = pl.DataFrame({"code": codes})
        findings = run_drift_checks(df, baseline)
        pat_findings = [f for f in findings if f.check == "pattern_drift"]
        assert len(pat_findings) >= 1


class TestDriftFindingFormat:
    def test_source_is_baseline_drift(self):
        baseline = BaselineProfile(
            rows=1000, columns=1,
            statistical_profiles={
                "val": StatProfile(entropy=5.0, bounds={"p01": 0, "p99": 100}),
            },
        )
        df = pl.DataFrame({"val": [999] * 100})
        findings = run_drift_checks(df, baseline)
        for f in findings:
            assert f.source == "baseline_drift"
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `cd D:/show_case/goldencheck && python -m pytest tests/drift/test_detector.py -v`
Expected: ImportError

- [ ] **Step 3: Implement drift detector**

```python
# goldencheck/drift/__init__.py
"""Drift detection — compare current data against a baseline profile."""
from __future__ import annotations

from goldencheck.drift.detector import run_drift_checks

__all__ = ["run_drift_checks"]
```

```python
# goldencheck/drift/detector.py
"""Drift detector — compare current data against baseline."""
from __future__ import annotations

import logging
import re

import polars as pl

from goldencheck.baseline.models import BaselineProfile
from goldencheck.models.finding import Finding, Severity

logger = logging.getLogger(__name__)

try:
    import numpy as np
    from scipy import stats as sp_stats
except ImportError:
    raise ImportError(
        "Install goldencheck[baseline] for drift detection: pip install goldencheck[baseline]"
    )


def run_drift_checks(
    df: pl.DataFrame, baseline: BaselineProfile,
) -> list[Finding]:
    """Run all drift checks against a baseline."""
    findings: list[Finding] = []
    findings.extend(_check_statistical(df, baseline))
    findings.extend(_check_constraints(df, baseline))
    findings.extend(_check_patterns(df, baseline))
    findings.extend(_check_correlations(df, baseline))
    return findings


def _check_statistical(df: pl.DataFrame, baseline: BaselineProfile) -> list[Finding]:
    """Check for distribution drift, entropy changes, bound violations."""
    findings = []

    for col, profile in baseline.statistical_profiles.items():
        if col not in df.columns:
            continue
        series = df[col]
        if not series.dtype.is_numeric():
            continue

        values = series.drop_nulls().to_numpy().astype(float)
        if len(values) < 30:
            continue

        # Bound violations
        if profile.bounds:
            p01 = profile.bounds.get("p01")
            p99 = profile.bounds.get("p99")
            if p01 is not None and p99 is not None:
                below = int((values < p01).sum())
                above = int((values > p99).sum())
                total_violations = below + above
                if total_violations > len(values) * 0.05:
                    findings.append(Finding(
                        severity=Severity.ERROR,
                        column=col,
                        check="bound_violation",
                        message=f"{total_violations} values outside baseline bounds [{p01}, {p99}]",
                        source="baseline_drift",
                        confidence=0.9,
                        metadata={"technique": "statistical", "drift_type": "bound_violation",
                                  "below_count": below, "above_count": above},
                    ))

        # Distribution drift via KS-test
        if profile.distribution and profile.params:
            dist_map = {
                "normal": sp_stats.norm,
                "log_normal": sp_stats.lognorm,
                "exponential": sp_stats.expon,
                "uniform": sp_stats.uniform,
            }
            dist_fn = dist_map.get(profile.distribution)
            if dist_fn:
                try:
                    params = tuple(profile.params.values())
                    _, p = sp_stats.kstest(values, dist_fn.cdf, args=params)
                    if p < 0.01:
                        findings.append(Finding(
                            severity=Severity.ERROR,
                            column=col,
                            check="distribution_drift",
                            message=f"Distribution shifted from {profile.distribution} — KS p-value: {p:.4f}",
                            source="baseline_drift",
                            confidence=0.95,
                            metadata={"technique": "statistical", "drift_type": "distribution_shift",
                                      "baseline_distribution": profile.distribution, "ks_pvalue": float(p)},
                        ))
                    elif p < 0.05:
                        findings.append(Finding(
                            severity=Severity.WARNING,
                            column=col,
                            check="distribution_drift",
                            message=f"Distribution may have shifted from {profile.distribution} — KS p-value: {p:.4f}",
                            source="baseline_drift",
                            confidence=0.8,
                            metadata={"technique": "statistical", "drift_type": "distribution_shift",
                                      "baseline_distribution": profile.distribution, "ks_pvalue": float(p)},
                        ))
                except Exception:
                    logger.debug("KS-test failed for %s", col)

    return findings


def _check_constraints(df: pl.DataFrame, baseline: BaselineProfile) -> list[Finding]:
    """Check FD violations against baseline."""
    findings = []

    for fd in baseline.constraints_fd:
        det_cols = [c for c in fd.determinant if c in df.columns]
        dep_cols = [c for c in fd.dependent if c in df.columns]
        if not det_cols or not dep_cols:
            continue

        for dep in dep_cols:
            grouped = df.group_by(det_cols).agg(pl.col(dep).n_unique().alias("n_dep"))
            violations = grouped.filter(pl.col("n_dep") > 1).height
            total = grouped.height
            if total == 0:
                continue
            violation_rate = violations / total
            baseline_violation = 1 - fd.confidence

            if violation_rate > max(baseline_violation * 2, 0.05):
                findings.append(Finding(
                    severity=Severity.ERROR,
                    column=dep,
                    check="fd_violation",
                    message=f"FD {' + '.join(det_cols)} → {dep} violation rate: {violation_rate:.1%} (baseline: {baseline_violation:.1%})",
                    source="baseline_drift",
                    confidence=0.9,
                    metadata={"technique": "constraints", "drift_type": "fd_violation",
                              "determinant": det_cols, "violation_rate": violation_rate},
                ))

    return findings


def _check_patterns(df: pl.DataFrame, baseline: BaselineProfile) -> list[Finding]:
    """Check pattern grammar coverage against baseline."""
    findings = []

    for col, grammars in baseline.patterns.items():
        if col not in df.columns or df[col].dtype != pl.Utf8:
            continue

        values = df[col].drop_nulls().to_list()
        if not values:
            continue

        for grammar in grammars:
            try:
                regex = re.compile(f"^{grammar.pattern}$")
            except re.error:
                continue

            matches = sum(1 for v in values if regex.match(v))
            current_coverage = matches / len(values)

            if grammar.coverage - current_coverage > 0.05:
                findings.append(Finding(
                    severity=Severity.WARNING,
                    column=col,
                    check="pattern_drift",
                    message=f"Pattern '{grammar.pattern}' coverage dropped: {grammar.coverage:.0%} → {current_coverage:.0%}",
                    source="baseline_drift",
                    confidence=0.85,
                    metadata={"technique": "patterns", "drift_type": "coverage_drop",
                              "baseline_coverage": grammar.coverage, "current_coverage": current_coverage},
                ))

    return findings


def _check_correlations(df: pl.DataFrame, baseline: BaselineProfile) -> list[Finding]:
    """Check correlation coefficient changes."""
    findings = []

    for entry in baseline.correlations:
        cols = entry.columns
        if not all(c in df.columns for c in cols):
            continue
        if len(cols) != 2:
            continue
        a, b = cols

        if entry.measure == "pearson" and df[a].dtype.is_numeric() and df[b].dtype.is_numeric():
            clean = df.select([a, b]).drop_nulls()
            if clean.height < 30:
                continue
            va = clean[a].to_numpy().astype(float)
            vb = clean[b].to_numpy().astype(float)
            r, _ = sp_stats.pearsonr(va, vb)
            delta = abs(entry.value - r)
            if delta > 0.1 and entry.strength == "strong":
                findings.append(Finding(
                    severity=Severity.WARNING,
                    column=f"{a}, {b}",
                    check="correlation_break",
                    message=f"Correlation dropped: {entry.value:.2f} → {r:.2f}",
                    source="baseline_drift",
                    confidence=0.8,
                    metadata={"technique": "correlation", "drift_type": "correlation_break",
                              "baseline_value": entry.value, "current_value": float(r)},
                ))

    return findings
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `cd D:/show_case/goldencheck && python -m pytest tests/drift/test_detector.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add goldencheck/drift/__init__.py goldencheck/drift/detector.py tests/drift/__init__.py tests/drift/test_detector.py
git commit -m "feat(drift): add drift detector — statistical, constraint, pattern, correlation checks"
```

---

## Task 11: Integrate into Scanner

**Files:**
- Modify: `goldencheck/engine/scanner.py:209-282` — add `baseline` param
- Create: `tests/drift/test_integration.py`

- [ ] **Step 1: Write integration test**

```python
# tests/drift/test_integration.py
"""Integration test — scan_file with baseline parameter."""
from __future__ import annotations

from pathlib import Path
import polars as pl
import pytest
from goldencheck import scan_file
from goldencheck.baseline import create_baseline


class TestScanWithBaseline:
    def test_scan_without_baseline_unchanged(self):
        findings, profile = scan_file(Path("tests/fixtures/messy.csv"))
        assert isinstance(findings, list)
        assert profile.row_count > 0

    def test_scan_with_baseline_adds_drift_findings(self, tmp_path: Path):
        # Create baseline from clean data
        clean_df = pl.DataFrame({
            "id": list(range(500)),
            "email": [f"user{i}@example.com" for i in range(500)],
            "age": [25 + (i % 50) for i in range(500)],
        })
        csv_path = tmp_path / "clean.csv"
        clean_df.write_csv(csv_path)
        baseline = create_baseline(csv_path)

        # Modify data — shift age distribution
        drifted_df = pl.DataFrame({
            "id": list(range(500)),
            "email": [f"user{i}@example.com" for i in range(500)],
            "age": [80 + (i % 20) for i in range(500)],
        })
        drifted_path = tmp_path / "drifted.csv"
        drifted_df.write_csv(drifted_path)

        findings, profile = scan_file(drifted_path, baseline=baseline)
        drift_findings = [f for f in findings if f.source == "baseline_drift"]
        assert len(drift_findings) > 0

    def test_scan_with_baseline_path(self, tmp_path: Path):
        df = pl.DataFrame({"x": list(range(200))})
        csv_path = tmp_path / "data.csv"
        df.write_csv(csv_path)

        baseline = create_baseline(csv_path)
        baseline_path = tmp_path / "baseline.yaml"
        baseline.save(baseline_path)

        findings, profile = scan_file(csv_path, baseline=baseline_path)
        assert isinstance(findings, list)
```

- [ ] **Step 2: Run test — verify it fails**

Run: `cd D:/show_case/goldencheck && python -m pytest tests/drift/test_integration.py -v`
Expected: TypeError — `scan_file()` does not accept `baseline`

- [ ] **Step 3: Add `baseline` parameter to `scan_file()`**

In `goldencheck/engine/scanner.py`, modify the `scan_file` signature (line 209) to add:

```python
def scan_file(
    path: Path,
    sample_size: int = 100_000,
    return_sample: bool = False,
    domain: str | None = None,
    baseline: "BaselineProfile | Path | None" = None,
) -> tuple[list[Finding], DatasetProfile] | tuple[list[Finding], DatasetProfile, pl.DataFrame]:
```

Then, after the corroboration boost step (around line 277), add drift detection:

```python
    # --- Baseline drift detection ---
    if baseline is not None:
        from pathlib import Path as _Path
        if isinstance(baseline, (_Path, str)):
            from goldencheck.baseline import load_baseline
            baseline = load_baseline(baseline)
        from goldencheck.drift import run_drift_checks
        import logging as _logging
        _logger = _logging.getLogger(__name__)
        if baseline.source_filename and baseline.source_filename != path.name:
            _logger.warning(
                "Baseline source '%s' doesn't match scan file '%s'",
                baseline.source_filename, path.name,
            )
        drift_findings = run_drift_checks(sample, baseline)
        all_findings.extend(drift_findings)
```

- [ ] **Step 4: Run integration test — verify it passes**

Run: `cd D:/show_case/goldencheck && python -m pytest tests/drift/test_integration.py -v`
Expected: All pass

- [ ] **Step 5: Run full test suite to check for regressions**

Run: `cd D:/show_case/goldencheck && python -m pytest --tb=short -v`
Expected: All existing tests still pass

- [ ] **Step 6: Commit**

```bash
git add goldencheck/engine/scanner.py tests/drift/test_integration.py
git commit -m "feat(scanner): integrate baseline drift detection into scan_file()"
```

---

## Task 12: CLI — Baseline Command + Scan Flags

**Files:**
- Modify: `goldencheck/cli/main.py` — add `baseline` command, `--baseline`/`--no-baseline` on scan, update hand-rolled parser

- [ ] **Step 1: Add `baseline` CLI command**

After the existing `learn` command (around line 310), add:

```python
@app.command()
def baseline(
    file: Path = typer.Argument(..., help="CSV/Parquet/Excel file to profile"),
    output: Path = typer.Option(None, "-o", "--output", help="Output YAML path"),
    skip: list[str] = typer.Option([], "--skip", help="Techniques to skip: statistical, constraints, semantic, correlation, patterns, priors"),
    update: bool = typer.Option(False, "--update", help="Update existing baseline instead of replacing"),
) -> None:
    """Create a deep profiling baseline for drift detection."""
    try:
        from goldencheck.baseline import create_baseline, load_baseline
    except ImportError:
        console.print("[red]Install goldencheck[baseline]: pip install goldencheck[baseline][/]")
        raise typer.Exit(1)

    out_path = output or Path("goldencheck_baseline.yaml")

    if update and out_path.exists():
        console.print(f"[yellow]Updating existing baseline: {out_path}[/]")
        existing = load_baseline(out_path)
        new = create_baseline(file, source=str(file), skip=skip)
        existing.update_from(new)
        existing.save(out_path)
    else:
        console.print(f"[cyan]Creating baseline for {file}...[/]")
        profile = create_baseline(file, source=str(file), skip=skip)
        profile.save(out_path)

    console.print(f"[green]Baseline saved to {out_path}[/]")
```

- [ ] **Step 2: Add `--baseline` and `--no-baseline` flags to `scan` command**

Modify the `scan()` command signature to add:

```python
    baseline_path: Path = typer.Option(None, "--baseline", help="Path to baseline YAML"),
    no_baseline: bool = typer.Option(False, "--no-baseline", help="Ignore baseline files"),
```

Pass to `_do_scan()`:
```python
    _do_scan(file, ..., baseline_path=baseline_path, no_baseline=no_baseline)
```

- [ ] **Step 3: Update `_do_scan()` to accept and use baseline**

Add parameters to `_do_scan`:
```python
    baseline_path: Path | None = None,
    no_baseline: bool = False,
```

In the scan section, load baseline:
```python
    # Load baseline
    baseline = None
    if not no_baseline:
        if baseline_path:
            from goldencheck.baseline import load_baseline
            baseline = load_baseline(baseline_path)
        else:
            # Auto-detect
            for candidate in [Path("goldencheck_baseline.yaml"), file.parent / "goldencheck_baseline.yaml"]:
                if candidate.exists():
                    from goldencheck.baseline import load_baseline
                    baseline = load_baseline(candidate)
                    break
```

Then pass `baseline=baseline` to `scan_file()`.

- [ ] **Step 4: Update hand-rolled parser in `main()` callback**

In the `main()` callback (lines 75-165), add `--baseline` and `--no-baseline` to the known flags:

```python
    # Add to flag parsing section:
    baseline_path = None
    no_baseline = False
    # ... in the arg parsing loop:
    elif arg == "--baseline" and i + 1 < len(args):
        baseline_path = Path(args[i + 1])
        i += 2
        continue
    elif arg == "--no-baseline":
        no_baseline = True
        i += 1
        continue
```

And pass them to `_do_scan()`.

- [ ] **Step 5: Test CLI commands manually**

Run: `cd D:/show_case/goldencheck && goldencheck baseline tests/fixtures/messy.csv -o /tmp/test_baseline.yaml`
Expected: Baseline YAML created

Run: `cd D:/show_case/goldencheck && goldencheck scan tests/fixtures/messy.csv --baseline /tmp/test_baseline.yaml --no-tui`
Expected: Findings include drift results

- [ ] **Step 6: Run full test suite**

Run: `cd D:/show_case/goldencheck && python -m pytest --tb=short -v`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add goldencheck/cli/main.py
git commit -m "feat(cli): add baseline command and --baseline/--no-baseline scan flags"
```

---

## Task 13: Public API Exports + Finding HTML

**Files:**
- Modify: `goldencheck/__init__.py` — add lazy exports
- Modify: `goldencheck/models/finding.py` — update `_repr_html_()` for drift source

- [ ] **Step 1: Add lazy exports to `__init__.py`**

At the bottom of `goldencheck/__init__.py`, add:

```python
def __getattr__(name: str):
    if name == "create_baseline":
        from goldencheck.baseline import create_baseline
        return create_baseline
    if name == "load_baseline":
        from goldencheck.baseline import load_baseline
        return load_baseline
    raise AttributeError(f"module 'goldencheck' has no attribute {name!r}")
```

Add to `__all__`:
```python
    "create_baseline",
    "load_baseline",
```

- [ ] **Step 2: Update `Finding._repr_html_()` for drift source**

In `goldencheck/models/finding.py`, check if `_repr_html_` exists. If it does, add handling for `source="baseline_drift"` to show a "[DRIFT]" label. If it doesn't exist, add the method:

```python
    def _repr_html_(self) -> str:
        source_badge = ""
        if self.source == "llm":
            source_badge = ' <span style="color: #9b59b6;">[LLM]</span>'
        elif self.source == "baseline_drift":
            source_badge = ' <span style="color: #e67e22;">[DRIFT]</span>'
        return (
            f"<b>[{self.severity.name}]</b> {self.column}: "
            f"{self.check}{source_badge} — {self.message}"
        )
```

- [ ] **Step 3: Run full test suite**

Run: `cd D:/show_case/goldencheck && python -m pytest --tb=short -v`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add goldencheck/__init__.py goldencheck/models/finding.py
git commit -m "feat: add baseline exports and drift badge to Finding HTML"
```

---

## Task 14: Missing Drift Checks + Prior Integration + Pattern Suppression + Update Method

This task addresses review findings: complete the drift detector (9 missing check types), wire priors into the scan pipeline, add pattern suppression, and implement `update_from()`.

**Files:**
- Modify: `goldencheck/drift/detector.py` — add all 13 drift check types
- Modify: `goldencheck/engine/scanner.py` — wire priors before corroboration, add pattern suppression
- Modify: `goldencheck/baseline/models.py` — add `update_from()` method
- Modify: `tests/drift/test_detector.py` — add tests for missing checks

- [ ] **Step 1: Add missing drift checks to detector.py**

Add `_check_semantic()` to `run_drift_checks()`:

```python
def run_drift_checks(
    df: pl.DataFrame, baseline: BaselineProfile,
) -> list[Finding]:
    findings: list[Finding] = []
    findings.extend(_check_statistical(df, baseline))
    findings.extend(_check_constraints(df, baseline))
    findings.extend(_check_patterns(df, baseline))
    findings.extend(_check_correlations(df, baseline))
    findings.extend(_check_semantic(df, baseline))
    return findings
```

Add **entropy_drift** and **benford_drift** to `_check_statistical()`:

```python
        # Entropy drift (works for both numeric and string columns)
        if profile.entropy is not None:
            from goldencheck.baseline.statistical import _entropy, _entropy_numeric
            if series.dtype.is_numeric():
                current_entropy = _entropy_numeric(values)
            else:
                current_entropy = _entropy(series.drop_nulls().to_list())
            delta = abs(current_entropy - profile.entropy)
            if delta > 0.5:
                findings.append(Finding(
                    severity=Severity.WARNING, column=col, check="entropy_drift",
                    message=f"Entropy shifted: {profile.entropy:.2f} → {current_entropy:.2f} (delta={delta:.2f})",
                    source="baseline_drift", confidence=0.8,
                    metadata={"technique": "statistical", "drift_type": "entropy_shift",
                              "baseline_entropy": profile.entropy, "current_entropy": current_entropy},
                ))

        # Benford drift
        if profile.benford is not None:
            from goldencheck.baseline.statistical import _check_benford
            current = _check_benford(values)
            if current["passes"] != profile.benford.get("passes", False):
                findings.append(Finding(
                    severity=Severity.WARNING, column=col, check="benford_drift",
                    message=f"Benford's Law conformance changed: {profile.benford.get('passes')} → {current['passes']}",
                    source="baseline_drift", confidence=0.75,
                    metadata={"technique": "statistical", "drift_type": "benford_flip"},
                ))
```

Add **key_uniqueness_loss** and **temporal_order_drift** to `_check_constraints()`:

```python
    # Candidate key uniqueness loss
    for key in baseline.constraints_keys:
        cols = key.get("columns", [])
        if not all(c in df.columns for c in cols):
            continue
        if len(cols) == 1:
            series = df[cols[0]]
            if series.null_count() > 0 or series.n_unique() < df.height:
                findings.append(Finding(
                    severity=Severity.ERROR, column=cols[0], check="key_uniqueness_loss",
                    message=f"Candidate key '{cols[0]}' lost uniqueness: {series.n_unique()}/{df.height} unique",
                    source="baseline_drift", confidence=0.95,
                    metadata={"technique": "constraints", "drift_type": "key_uniqueness_loss"},
                ))

    # Temporal order drift
    for to in baseline.constraints_temporal:
        if to.before not in df.columns or to.after not in df.columns:
            continue
        try:
            before_dates = df[to.before].cast(pl.Date)
            after_dates = df[to.after].cast(pl.Date)
            mask = (before_dates > after_dates).drop_nulls()
            if mask.len() == 0:
                continue
            current_rate = float(mask.sum() / mask.len())
            if current_rate > max(to.violation_rate * 2, 0.05):
                findings.append(Finding(
                    severity=Severity.WARNING, column=f"{to.before}, {to.after}",
                    check="temporal_order_drift",
                    message=f"Temporal order violation rate: {to.violation_rate:.1%} → {current_rate:.1%}",
                    source="baseline_drift", confidence=0.85,
                    metadata={"technique": "constraints", "drift_type": "temporal_order_drift"},
                ))
        except Exception:
            pass
```

Add `_check_semantic()` function:

```python
def _check_semantic(df: pl.DataFrame, baseline: BaselineProfile) -> list[Finding]:
    """Check for semantic type drift."""
    if not baseline.semantic_types:
        return []
    findings = []
    try:
        from goldencheck.baseline.semantic import infer_semantic_types
        current = infer_semantic_types(df, use_embeddings=False)  # fast keyword fallback
        # Invert both to column -> type
        baseline_col_type = {}
        for stype, cols in baseline.semantic_types.items():
            for c in cols:
                baseline_col_type[c] = stype
        current_col_type = {}
        for stype, cols in current.items():
            for c in cols:
                current_col_type[c] = stype
        for col, old_type in baseline_col_type.items():
            if col not in df.columns:
                continue
            new_type = current_col_type.get(col)
            if new_type and new_type != old_type:
                findings.append(Finding(
                    severity=Severity.WARNING, column=col, check="type_drift",
                    message=f"Semantic type changed: {old_type} → {new_type}",
                    source="baseline_drift", confidence=0.8,
                    metadata={"technique": "semantic", "drift_type": "type_change",
                              "baseline_type": old_type, "current_type": new_type},
                ))
    except Exception:
        logger.debug("Semantic drift check failed")
    return findings
```

Add **new_pattern** and **new_correlation** handling:

In `_check_patterns`, after the coverage drop check, add:
```python
        # Check for new format variants not in baseline
        from goldencheck.baseline.patterns import _induce_column_grammars
        current_grammars = _induce_column_grammars(values)
        baseline_patterns = {g.pattern for g in grammars}
        for g in current_grammars:
            if g.pattern not in baseline_patterns and g.coverage > 0.05:
                findings.append(Finding(
                    severity=Severity.INFO, column=col, check="new_pattern",
                    message=f"New format variant: '{g.pattern}' ({g.coverage:.0%} of values)",
                    source="baseline_drift", confidence=0.7,
                    metadata={"technique": "patterns", "drift_type": "new_variant"},
                ))
```

In `_check_correlations`, after the Pearson section, add Cramer's V handling and new correlation detection:
```python
        elif entry.measure == "cramers_v":
            if df[a].dtype == pl.Utf8 and df[b].dtype == pl.Utf8:
                from goldencheck.baseline.correlation import _cramers_v
                current_entry = _cramers_v(df, a, b)
                if current_entry:
                    delta = abs(entry.value - current_entry.value)
                    if delta > 0.1 and entry.strength == "strong":
                        findings.append(Finding(
                            severity=Severity.WARNING, column=f"{a}, {b}",
                            check="correlation_break",
                            message=f"Cramer's V dropped: {entry.value:.2f} → {current_entry.value:.2f}",
                            source="baseline_drift", confidence=0.8,
                            metadata={"technique": "correlation", "drift_type": "correlation_break"},
                        ))
```

- [ ] **Step 2: Add tests for missing checks**

Add to `tests/drift/test_detector.py`:

```python
class TestEntropyDrift:
    def test_detects_entropy_shift(self):
        baseline = BaselineProfile(
            rows=1000, columns=1,
            statistical_profiles={
                "status": StatProfile(entropy=1.0, bounds={}),
            },
        )
        # Very different entropy
        df = pl.DataFrame({"status": [f"val_{i}" for i in range(500)]})
        findings = run_drift_checks(df, baseline)
        entropy_findings = [f for f in findings if f.check == "entropy_drift"]
        assert len(entropy_findings) >= 1


class TestKeyUniquenessLoss:
    def test_detects_duplicate_key(self):
        baseline = BaselineProfile(
            rows=100, columns=1,
            constraints_keys=[{"columns": ["id"], "unique": True}],
        )
        df = pl.DataFrame({"id": [1, 1, 2, 3, 4] * 20})
        findings = run_drift_checks(df, baseline)
        key_findings = [f for f in findings if f.check == "key_uniqueness_loss"]
        assert len(key_findings) >= 1


class TestTemporalOrderDrift:
    def test_detects_increased_violations(self):
        baseline = BaselineProfile(
            rows=1000, columns=2,
            constraints_temporal=[
                TemporalOrder(before="start", after="end", violation_rate=0.01),
            ],
        )
        starts = ["2024-12-01"] * 50 + ["2024-01-01"] * 50
        ends = ["2024-01-01"] * 50 + ["2024-12-01"] * 50
        df = pl.DataFrame({"start": starts, "end": ends})
        findings = run_drift_checks(df, baseline)
        temporal_findings = [f for f in findings if f.check == "temporal_order_drift"]
        assert len(temporal_findings) >= 1
```

- [ ] **Step 3: Wire priors into scan_file() BEFORE corroboration boost**

In `goldencheck/engine/scanner.py`, in the baseline integration block, add prior application before `apply_corroboration_boost`:

```python
    # --- Apply baseline priors (before corroboration boost) ---
    if baseline is not None and hasattr(baseline, 'confidence_priors') and baseline.confidence_priors:
        from goldencheck.baseline.priors import apply_prior
        from goldencheck.baseline.models import ConfidencePrior
        for i, finding in enumerate(all_findings):
            check_priors = baseline.confidence_priors.get(finding.check, {})
            prior = check_priors.get(finding.column)
            if prior:
                new_conf = apply_prior(finding.confidence, prior)
                all_findings[i] = dataclasses.replace(finding, confidence=new_conf)
```

This block must go BEFORE the existing `apply_corroboration_boost(all_findings)` call, and BEFORE the drift detection block (since drift findings don't need prior adjustment). Add `import dataclasses` at the top of scanner.py if not already present.

- [ ] **Step 4: Add pattern suppression to scan_file()**

In `goldencheck/engine/scanner.py`, after drift findings are added, suppress `pattern_consistency` findings for columns covered by baseline patterns:

```python
    # --- Suppress PatternConsistencyProfiler findings for baseline-covered columns ---
    if baseline is not None and baseline.patterns:
        baseline_pattern_cols = set(baseline.patterns.keys())
        all_findings = [
            f for f in all_findings
            if not (f.check == "pattern_consistency" and f.column in baseline_pattern_cols)
        ]
```

- [ ] **Step 5: Add `update_from()` method to BaselineProfile**

In `goldencheck/baseline/models.py`, add to the `BaselineProfile` class:

```python
    def update_from(self, new: "BaselineProfile") -> None:
        """Merge another baseline into this one using spec merge semantics."""
        from datetime import datetime, timezone

        # Record history
        self.history.append({"source": self.source, "created": self.created})
        self.created = datetime.now(timezone.utc).isoformat()
        self.source = new.source
        self.rows = new.rows
        self.columns = new.columns

        # Statistical: refit from new data
        self.statistical_profiles = new.statistical_profiles

        # Constraints: keep FDs with confidence >= 0.8 in new, add new high-confidence
        surviving = [fd for fd in self.constraints_fd if any(
            fd.determinant == nfd.determinant and nfd.confidence >= 0.8
            for nfd in new.constraints_fd
        )]
        for nfd in new.constraints_fd:
            if nfd.confidence >= 0.9 and not any(
                fd.determinant == nfd.determinant and fd.dependent == nfd.dependent
                for fd in surviving
            ):
                surviving.append(nfd)
        self.constraints_fd = surviving
        self.constraints_keys = new.constraints_keys
        self.constraints_temporal = new.constraints_temporal

        # Semantic: keep unless >50% shift (use new)
        self.semantic_types = new.semantic_types

        # Correlations: replace on >0.15 delta
        self.correlations = new.correlations

        # Patterns: union grammars
        for col, new_grammars in new.patterns.items():
            if col in self.patterns:
                existing_patterns = {g.pattern for g in self.patterns[col]}
                for g in new_grammars:
                    if g.pattern not in existing_patterns and g.coverage > 0.05:
                        self.patterns[col].append(g)
                # Recalculate coverages — just keep new values
                self.patterns[col] = new_grammars
            else:
                self.patterns[col] = new_grammars

        # Priors: weighted average
        for check, cols in new.confidence_priors.items():
            if check not in self.confidence_priors:
                self.confidence_priors[check] = cols
                continue
            for col, new_prior in cols.items():
                if col in self.confidence_priors[check]:
                    old = self.confidence_priors[check][col]
                    total = old.evidence_count + new_prior.evidence_count
                    merged_conf = (
                        (old.confidence * old.evidence_count + new_prior.confidence * new_prior.evidence_count)
                        / total
                    ) if total > 0 else new_prior.confidence
                    self.confidence_priors[check][col] = ConfidencePrior(
                        confidence=round(merged_conf, 4),
                        evidence_count=total,
                    )
                else:
                    self.confidence_priors[check][col] = new_prior
```

- [ ] **Step 6: Add update_from test**

Add to `tests/baseline/test_models.py`:

```python
class TestUpdateFrom:
    def test_merges_baselines(self):
        old = BaselineProfile(
            source="old.csv", rows=1000, columns=3,
            constraints_fd=[
                FunctionalDependency(determinant=["zip"], dependent=["city"], confidence=0.95),
            ],
            confidence_priors={
                "format_detection": {
                    "email": ConfidencePrior(confidence=0.9, evidence_count=1000),
                },
            },
        )
        new = BaselineProfile(
            source="new.csv", rows=2000, columns=3,
            constraints_fd=[
                FunctionalDependency(determinant=["zip"], dependent=["city"], confidence=0.90),
            ],
            confidence_priors={
                "format_detection": {
                    "email": ConfidencePrior(confidence=0.85, evidence_count=2000),
                },
            },
        )
        old.update_from(new)
        assert old.source == "new.csv"
        assert old.rows == 2000
        assert len(old.history) == 1
        assert old.history[0]["source"] == "old.csv"
        # Priors should be weighted average
        email_prior = old.confidence_priors["format_detection"]["email"]
        assert email_prior.evidence_count == 3000
        expected_conf = (0.9 * 1000 + 0.85 * 2000) / 3000
        assert abs(email_prior.confidence - round(expected_conf, 4)) < 0.001
```

- [ ] **Step 7: Run all tests**

Run: `cd D:/show_case/goldencheck && python -m pytest --tb=short -v`
Expected: All pass

- [ ] **Step 8: Commit**

```bash
git add goldencheck/drift/detector.py goldencheck/engine/scanner.py goldencheck/baseline/models.py tests/drift/test_detector.py tests/baseline/test_models.py
git commit -m "feat: complete drift checks, prior integration, pattern suppression, update_from"
```

---

## Task 15: _repr_html_ — Extend, Don't Replace

**Important:** Task 13 Step 2 must **extend** the existing `_repr_html_()` method, not replace it. Read the existing implementation first and add the `[DRIFT]` badge alongside the existing `[LLM]` badge logic without removing any existing styling or content.

- [ ] **Step 1: Read existing `_repr_html_()` implementation**

Run: Read `goldencheck/models/finding.py` lines 27-42

- [ ] **Step 2: Add drift badge to existing method**

Find the section that handles `source == "llm"` and add an equivalent block for `source == "baseline_drift"` that renders a `[DRIFT]` badge with color `#e67e22`. Do not change any other part of the method.

- [ ] **Step 3: Run tests and commit**

```bash
python -m pytest tests/models/ -v
git add goldencheck/models/finding.py
git commit -m "feat: add DRIFT badge to Finding HTML renderer"
```

---

## Task 16: Ruff + Final Verification

- [ ] **Step 1: Run ruff**

Run: `cd D:/show_case/goldencheck && python -m ruff check .`
Expected: All checks pass. If not, fix issues.

- [ ] **Step 2: Run full test suite**

Run: `cd D:/show_case/goldencheck && python -m pytest --tb=short -v`
Expected: All tests pass, including new baseline and drift tests.

- [ ] **Step 3: Verify DQBench score is not regressed**

Run: `cd D:/show_case/goldencheck && python -c "from goldencheck import scan_file; from pathlib import Path; f,p = scan_file(Path('tests/fixtures/messy.csv')); print(f'{len(f)} findings, {p.row_count} rows')"`
Expected: Same number of findings as before (no regressions from baseline integration).

- [ ] **Step 4: Commit any final fixes**

```bash
git add -A
git commit -m "chore: lint fixes and final verification"
```
