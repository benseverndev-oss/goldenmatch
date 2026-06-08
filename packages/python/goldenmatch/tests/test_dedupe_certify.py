"""dedupe_df(..., certify=True) attaches an unsupervised RecallEstimate.

The estimate runs each decorrelated matchkey/pass system through the pipeline and
applies the FP-aware capture-recapture estimator (the same machinery the
``evaluate --certify`` CLI uses). Off by default.
"""

from __future__ import annotations

import polars as pl
from goldenmatch import RecallEstimate, dedupe_df
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)


def _config() -> GoldenMatchConfig:
    # One 3-field weighted matchkey -> build_decorrelated_systems splits it into
    # 3 per-field systems, which is the >=3 minimum estimate_recall needs.
    return GoldenMatchConfig(
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["last_name"], transforms=["soundex"])],
        ),
        matchkeys=[
            MatchkeyConfig(
                name="identity",
                type="weighted",
                threshold=0.7,
                fields=[
                    MatchkeyField(field="first_name", scorer="jaro_winkler", weight=1.0),
                    MatchkeyField(field="last_name", scorer="jaro_winkler", weight=1.0),
                    MatchkeyField(field="email", scorer="jaro_winkler", weight=0.8),
                ],
            )
        ],
    )


def _df() -> pl.DataFrame:
    # 12 rows, 4 duplicate pairs; surnames spread across distinct soundex codes
    # so soundex blocking makes several small blocks (no mega-block hang).
    return pl.DataFrame(
        {
            "first_name": [
                "John", "Jon", "Mary", "Mari", "Robert", "Bob",
                "Susan", "Sue", "Peter", "Linda", "Karl", "Omar",
            ],
            "last_name": [
                "Smith", "Smith", "Jones", "Jones", "Brown", "Brown",
                "Davis", "Davis", "Wilson", "Taylor", "Klein", "Hassan",
            ],
            "email": [
                "j@x.com", "j@x.com", "m@x.com", "m@x.com", "r@x.com", "r@x.com",
                "s@x.com", "s@x.com", "p@x.com", "l@x.com", "k@x.com", "o@x.com",
            ],
        }
    )


def test_certify_attaches_recall_estimate() -> None:
    res = dedupe_df(_df(), config=_config(), certify=True)
    assert res.recall_certificate is not None
    assert isinstance(res.recall_certificate, RecallEstimate)
    # 3-field matchkey -> 3 decorrelated systems.
    assert res.recall_certificate.n_systems == 3


def test_no_certify_leaves_certificate_none() -> None:
    res = dedupe_df(_df(), config=_config())
    assert res.recall_certificate is None
