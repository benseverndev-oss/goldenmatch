"""Parity + selection gate for the owned distribution fits (``_distributions``).

* The closed-form ``norm`` / ``expon`` / ``uniform`` fits + log-pdfs are
  **byte-identical** to ``scipy.stats``.
* ``lognorm`` is the standard two-parameter (``loc=0``) MLE -- a deliberate
  divergence from scipy's unstable free-``loc`` fit; asserted against the
  closed-form definition and checked for distribution-*selection* agreement.
* ``_fit_distribution`` selects the correct distribution for clearly-generated
  data of each shape (the guarantee goldencheck actually depends on).

scipy is a test-only oracle.
"""
from __future__ import annotations

import numpy as np
import pytest
from goldencheck.baseline._distributions import EXPON, LOGNORM, NORM, UNIFORM
from goldencheck.baseline.statistical import _fit_distribution

scipy_stats = pytest.importorskip("scipy.stats")


@pytest.mark.parametrize("seed", range(30))
def test_closed_form_fits_match_scipy(seed: int) -> None:
    rng = np.random.default_rng(seed)
    n = int(rng.integers(50, 600))
    for owned, scipy_d, x in [
        (NORM, scipy_stats.norm, rng.normal(3.0, 2.0, n)),
        (EXPON, scipy_stats.expon, rng.exponential(2.0, n) + 1.0),
        (UNIFORM, scipy_stats.uniform, rng.uniform(4.0, 9.0, n)),
    ]:
        op = owned.fit(x)
        sp = scipy_d.fit(x)
        assert op == pytest.approx(sp, abs=1e-12)
        # log-likelihood identical too (feeds AIC).
        assert float(owned.logpdf(x, *op).sum()) == pytest.approx(
            float(scipy_d.logpdf(x, *sp).sum()), abs=1e-9
        )


@pytest.mark.parametrize("seed", range(20))
def test_lognorm_fit_is_closed_form_loc0(seed: int) -> None:
    rng = np.random.default_rng(seed)
    x = rng.lognormal(0.5, 0.6, 400)
    s, loc, scale = LOGNORM.fit(x)
    lnx = np.log(x)
    assert loc == 0.0
    assert s == pytest.approx(float(lnx.std()), abs=1e-12)  # ddof=0
    assert scale == pytest.approx(float(np.exp(lnx.mean())), abs=1e-12)
    # A proper log density: integrates sensibly, finite on the sample.
    assert np.all(np.isfinite(LOGNORM.logpdf(x, s, loc, scale)))


def test_fit_distribution_selects_correct_shape() -> None:
    # Clearly-shaped data selects its own family the large majority of the time.
    # (Not 100%: with fitted -- not fully-specified -- params the KS gate is
    # biased at support boundaries, so uniform occasionally loses the gate and
    # falls back; scipy's fit exhibits the same effect. This asserts the owned
    # fit does its job, not a brittle exact count.)
    rng = np.random.default_rng(0)
    hits = {"normal": 0, "log_normal": 0, "exponential": 0, "uniform": 0}
    trials = 40
    for _ in range(trials):
        n = int(rng.integers(200, 600))
        if _fit_distribution(rng.normal(0.0, 1.0, n))[0] == "normal":
            hits["normal"] += 1
        if _fit_distribution(rng.lognormal(0.0, 0.5, n))[0] == "log_normal":
            hits["log_normal"] += 1
        if _fit_distribution(rng.exponential(2.0, n))[0] == "exponential":
            hits["exponential"] += 1
        if _fit_distribution(rng.uniform(0.0, 1.0, n))[0] == "uniform":
            hits["uniform"] += 1
    assert hits["normal"] >= 38
    assert hits["log_normal"] >= 38
    assert hits["exponential"] >= 38
    assert hits["uniform"] >= 30  # noisier (boundary-fit KS bias)


def test_fit_distribution_domain_guards() -> None:
    rng = np.random.default_rng(1)
    # Negative values: log_normal + exponential are skipped (need positivity).
    x = rng.normal(0.0, 1.0, 300)  # has negatives
    name, _ = _fit_distribution(x)
    assert name in ("normal", "uniform", None)
